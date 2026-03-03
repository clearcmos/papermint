#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
PIDDIR="$REPO_ROOT/.pids"
mkdir -p "$PIDDIR"

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

# Wrap a command through the appropriate environment
# On NixOS: runs inside nix develop so deps from flake.nix are available
# On other platforms: runs directly (assumes deps installed globally)
run() {
  case "$PLATFORM" in
    nixos)
      nix develop "$REPO_ROOT" --command bash -c "$*"
      ;;
    debian)
      # TODO: add venv / apt-based setup
      echo "[warn] Debian support not yet implemented — running directly."
      bash -c "$*"
      ;;
    macos)
      # TODO: add brew / venv-based setup
      echo "[warn] macOS support not yet implemented — running directly."
      bash -c "$*"
      ;;
    *)
      echo "[warn] Unknown platform — running directly."
      bash -c "$*"
      ;;
  esac
}

# Same as run() but launches in the background and sets _BG_PID.
# IMPORTANT: Do NOT call via $(...) — command substitution creates a subshell
# whose stdout fd is inherited by the background process, causing it to block.
_BG_PID=""
run_bg() {
  case "$PLATFORM" in
    nixos)
      nix develop "$REPO_ROOT" --command bash -c "$*" &
      ;;
    *)
      bash -c "$*" &
      ;;
  esac
  _BG_PID=$!
}

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
if [[ ${#PROJECTS[@]} -eq 1 ]]; then
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

echo
echo "Starting: $PROJECT"
echo "────────────────────────────"

# --- Kill any running instances for THIS project ---
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

kill_if_running "$PIDDIR/${PROJECT}.mint.pid" "$PROJECT mintlify"
kill_if_running "$PIDDIR/${PROJECT}.search.pid" "$PROJECT search API"

# --- Start Mintlify dev server ---
echo "Starting Mintlify dev server..."
run_bg "cd '$REPO_ROOT/$PROJECT' && npx mint dev"
MINT_PID=$_BG_PID
echo "$MINT_PID" > "$PIDDIR/${PROJECT}.mint.pid"

# --- Start search API ---
echo "Starting search API (PROJECT=$PROJECT)..."
run_bg "cd '$REPO_ROOT/search' && PROJECT='$PROJECT' python server.py"
SEARCH_PID=$_BG_PID
echo "$SEARCH_PID" > "$PIDDIR/${PROJECT}.search.pid"

echo
echo "────────────────────────────"
echo "Running:"
echo "  Mintlify  → http://localhost:3000  (PID $MINT_PID)"
echo "  Search    → http://localhost:3002  (PID $SEARCH_PID)"
echo
echo "Press Ctrl+C to stop both."

# --- Trap Ctrl+C to clean up both processes ---
cleanup() {
  echo
  echo "Shutting down..."
  kill "$MINT_PID" 2>/dev/null || true
  kill "$SEARCH_PID" 2>/dev/null || true
  rm -f "$PIDDIR/${PROJECT}.mint.pid" "$PIDDIR/${PROJECT}.search.pid"
  wait 2>/dev/null
  echo "Done."
}
trap cleanup INT TERM

wait
