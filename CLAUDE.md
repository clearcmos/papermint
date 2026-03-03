# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**papermint** — a multi-project Mintlify documentation repo. Each subdirectory is an independent Mintlify site with its own `docs.json`, pages, and theme config. A shared search API provides hybrid keyword + semantic search and RAG across any project's docs.

## Repository Structure

```
papermint/
├── CLAUDE.md
├── .gitignore
├── flake.nix               # Nix dev shell (Node.js 22, Python 3.13 + deps)
├── flake.lock
├── start.sh                # Launch Mintlify + search API for a project
├── search/                 # Shared search API (FastAPI + Ollama)
│   ├── server.py           # Search + RAG service
│   ├── indexer.py          # Document loading, chunking, embedding, caching
│   ├── sync-docs.py        # Sync external .md files into a Mintlify project
│   ├── requirements.txt
│   ├── README.md
│   └── .cache/             # Per-project embedding cache (gitignored)
├── scripts/
│   └── new-project.sh      # Scaffold a new Mintlify project
├── claude-code/            # Example project: Claude Code docs
│   ├── docs.json
│   ├── index.mdx
│   ├── custom.js
│   ├── .gitignore
│   └── en/*.mdx            # Doc pages
└── <other-project>/        # Additional Mintlify projects
    ├── docs.json
    ├── index.mdx
    └── en/*.mdx
```

## Development Commands

### Start everything (preferred)

```bash
./start.sh                  # Auto-detects projects, launches Mintlify + search API
```

On NixOS this runs inside `nix develop` automatically. On other platforms it runs directly (requires Node.js 22+ and Python 3.13+ with deps installed).

### Run individually

```bash
cd claude-code && npx mint dev                          # Mintlify dev server on :3000
cd search && PROJECT=claude-code python server.py       # Search API on :3002
```

### Create a new project

```bash
./scripts/new-project.sh my-docs "My Documentation"
cd my-docs && npx mint dev
```

### Sync external docs

```bash
cd search
python sync-docs.py                              # /etc/nixos/docs -> nixos/
python sync-docs.py /path/to/docs --project foo  # custom source + project
```

## Architecture

### Per-Project Structure

Each project directory is a self-contained Mintlify site:

- **`docs.json`** — Mintlify config (navigation, theme, colors)
  - Default theme: `"mint"`, colors: primary `#0E0E0E`, light `#D4A27F`, dark `#0E0E0E`
  - Background: dark `#09090B`, light `#FDFDF7` (set via top-level `background.color`, not inside `colors`)
  - Dark mode default via both `appearance.default` and `modeToggle.default`
- **`index.mdx`** — landing page
- **`en/*.mdx`** — doc pages using Mintlify's native components (no imports needed)

### Adding a New Doc Page

1. Create `<project>/en/<slug>.mdx` with `---` frontmatter (title, description)
2. Add the page path to the navigation in `<project>/docs.json`
3. Use Mintlify's global components: `<Note>`, `<Warning>`, `<Tip>`, `<Steps>`, `<Step>`, `<Tabs>`, `<Tab>`, `<CardGroup>`, `<Card>`

### Search API (`search/`)

- **FastAPI service** — `/api/search` (hybrid keyword + semantic), `/api/ask` (RAG with SSE streaming), `/api/reindex`, `/api/health`
- **`indexer.py`** — document loading, chunking (~500 tokens with 50-token overlap), embedding via Ollama
- **`server.py`** — uses `PROJECT` env var to resolve which project to index
- **`sync-docs.py`** — syncs external `.md` files into a standalone Mintlify project with `--project` flag
- **Ollama models** — `bge-m3` for embeddings, `qwen2.5:7b-instruct` for RAG answers
- **`custom.js`** — per-project script injected into Mintlify pages for custom search modal (Cmd+K/Ctrl+K)
- **Embedding cache** — on first run, embeddings are computed via Ollama and saved to `search/.cache/<project>/`. On subsequent starts, if no docs changed (verified by SHA-256 fingerprint of all doc contents + model name), embeddings load from disk instantly. Cache invalidates automatically when any `.md`/`.mdx` file is edited or the embedding model changes.

**Environment variables:**

| Variable | Default | Description |
|---|---|---|
| `PROJECT` | `claude-code` | Project directory to index (relative to repo root) |
| `DOCS_DIR` | `../<PROJECT>/` | Override: absolute path to docs directory |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama API URL |
| `EMBED_MODEL` | `bge-m3` | Embedding model name |
| `CHAT_MODEL` | `qwen2.5:7b-instruct` | Chat/RAG model name |
| `PORT` | `3002` | Search API port |

### `start.sh`

Platform-aware launcher that:
1. Discovers projects (directories containing `docs.json`)
2. Prompts for selection if multiple projects exist
3. Launches both Mintlify dev server and search API as background processes
4. Cleans up both on Ctrl+C
