#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
PIDDIR="$REPO_ROOT/.pids"
LOGDIR="$REPO_ROOT/.logs"
LAST_PROJECT_FILE="$PIDDIR/.last-project"
mkdir -p "$PIDDIR" "$LOGDIR"

# --- Detect platform and set up environment wrapper ---
detect_platform() {
  if [[ -f /etc/NIXOS ]] || grep -qi nixos /etc/os-release 2>/dev/null; then
    echo "nixos"
  elif [[ -f /etc/debian_version ]]; then
    echo "debian"
  elif [[ "$(uname -s)" == "Darwin" ]]; then
    echo "macos"
  else
    echo "unknown"
  fi
}

PLATFORM="$(detect_platform)"

# Launch a command in the background, detached, with output to a log file.
# Sets _BG_PID to the process ID.
_BG_PID=""
run_bg() {
  local logfile="$1"; shift
  case "$PLATFORM" in
    nixos)
      nix develop "$REPO_ROOT" --command bash -c "$*" > "$logfile" 2>&1 &
      ;;
    *)
      bash -c "$*" > "$logfile" 2>&1 &
      ;;
  esac
  _BG_PID=$!
  disown $_BG_PID
}

# --- Kill helpers ---
kill_if_running() {
  local pidfile="$1" label="$2"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid=$(<"$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      echo "Stopping previous $label (PID $pid)..."
      kill "$pid" 2>/dev/null || true
      for _ in {1..10}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.3
      done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
}

kill_port() {
  local port="$1" label="$2"
  local pids
  pids=$(lsof -ti :"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "Killing leftover $label on port $port (PIDs: $(echo $pids | tr '\n' ' '))..."
    echo "$pids" | xargs kill 2>/dev/null || true
    sleep 0.5
    pids=$(lsof -ti :"$port" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
      echo "$pids" | xargs kill -9 2>/dev/null || true
      sleep 0.3
    fi
  fi
}

# --- Stop subcommand ---
if [[ "${1:-}" == "stop" ]]; then
  echo "Stopping all papermint services..."
  for pidfile in "$PIDDIR"/*.pid; do
    [[ -f "$pidfile" ]] || continue
    kill_if_running "$pidfile" "$(basename "$pidfile" .pid)"
  done
  kill_port 3000 "Mintlify"
  kill_port 3002 "Search API"
  echo "Stopped."
  exit 0
fi

# --- Restart subcommand ---
if [[ "${1:-}" == "restart" ]]; then
  if [[ ! -f "$LAST_PROJECT_FILE" ]]; then
    echo "No previous project found. Run ./start.sh first."
    exit 1
  fi
  PROJECT=$(<"$LAST_PROJECT_FILE")
  echo "Restarting: $PROJECT"
  # Re-exec as stop then start with the saved project
  "$0" stop
  exec "$0" "$PROJECT"
fi

# --- Logs subcommand ---
if [[ "${1:-}" == "logs" ]]; then
  project="${2:-}"
  if [[ -z "$project" ]]; then
    echo "Usage: ./start.sh logs <project> [mint|search]"
    echo "Available logs:"
    ls "$LOGDIR"/*.log 2>/dev/null | sed "s|$LOGDIR/||" || echo "  (none)"
    exit 0
  fi
  component="${3:-mint}"
  logfile="$LOGDIR/${project}.${component}.log"
  if [[ -f "$logfile" ]]; then
    tail -f "$logfile"
  else
    echo "No log file: $logfile"
    exit 1
  fi
fi

echo "Platform: $PLATFORM"
echo

# --- Discover projects (dirs containing docs.json) ---
mapfile -t PROJECTS < <(
  find "$REPO_ROOT" -maxdepth 2 -name docs.json -not -path '*/node_modules/*' \
    | sed "s|$REPO_ROOT/||;s|/docs.json||" | sort
)

if [[ ${#PROJECTS[@]} -eq 0 ]]; then
  echo "No Mintlify projects found (no docs.json)."
  exit 1
fi

# --- Project selection ---
# Accept project name as argument (used by restart)
if [[ -n "${1:-}" ]]; then
  PROJECT="$1"
  if [[ ! -f "$REPO_ROOT/$PROJECT/docs.json" ]]; then
    echo "Unknown project: $PROJECT"
    exit 1
  fi
elif [[ ${#PROJECTS[@]} -eq 1 ]]; then
  PROJECT="${PROJECTS[0]}"
  echo "Only one project found: $PROJECT"
else
  echo "Available projects:"
  for i in "${!PROJECTS[@]}"; do
    printf "  %d) %s\n" $((i + 1)) "${PROJECTS[$i]}"
  done
  echo
  read -rp "Select project [1-${#PROJECTS[@]}]: " choice
  if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#PROJECTS[@]} )); then
    echo "Invalid selection."
    exit 1
  fi
  PROJECT="${PROJECTS[$((choice - 1))]}"
fi

# Remember project for restart
echo "$PROJECT" > "$LAST_PROJECT_FILE"

echo
echo "Starting: $PROJECT"
echo "────────────────────────────"

# --- Kill previous instances ---
kill_if_running "$PIDDIR/${PROJECT}.mint.pid" "$PROJECT mintlify"
kill_if_running "$PIDDIR/${PROJECT}.search.pid" "$PROJECT search API"
kill_port 3000 "Mintlify"
kill_port 3002 "Search API"

# --- Start Mintlify dev server (detached) ---
echo "Starting Mintlify dev server..."
run_bg "$LOGDIR/${PROJECT}.mint.log" "cd '$REPO_ROOT/$PROJECT' && npx mint dev"
MINT_PID=$_BG_PID
echo "$MINT_PID" > "$PIDDIR/${PROJECT}.mint.pid"

# --- Start search API (detached) ---
echo "Starting search API (PROJECT=$PROJECT)..."
run_bg "$LOGDIR/${PROJECT}.search.log" "cd '$REPO_ROOT/search' && PROJECT='$PROJECT' python server.py"
SEARCH_PID=$_BG_PID
echo "$SEARCH_PID" > "$PIDDIR/${PROJECT}.search.pid"

echo
echo "────────────────────────────"
echo "Running (detached):"
echo "  Mintlify  → http://localhost:3000  (PID $MINT_PID)"
echo "  Search    → http://localhost:3002  (PID $SEARCH_PID)"
echo
echo "Commands:"
echo "  ./start.sh restart                 Restart last project"
echo "  ./start.sh stop                    Stop all services"
echo "  ./start.sh logs $PROJECT mint      Tail Mintlify logs"
echo "  ./start.sh logs $PROJECT search    Tail search API logs"
