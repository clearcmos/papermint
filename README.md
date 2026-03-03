# papermint

A multi-project [Mintlify](https://mintlify.com) documentation repo with built-in search. Each subdirectory is an independent doc site with its own config, pages, and theme. A shared FastAPI search service provides keyword search out of the box, with optional semantic search and RAG-powered Q&A when [Ollama](https://ollama.com) is available.

## Quick Start

### Prerequisites

- **Node.js 22+** (for Mintlify)
- **Python 3.13+** with `fastapi`, `uvicorn`, `httpx`, `numpy`
- **Ollama** *(optional)* — enables semantic search and AI Q&A. Pull `bge-m3` and `qwen2.5:7b-instruct`. Without Ollama, search falls back to keyword matching.

On **NixOS**, all deps (except Ollama) are provided automatically via `flake.nix` — just run `./start.sh`.

### Run a project

```bash
./start.sh
```

This auto-detects projects, starts the Mintlify dev server (`:3000`) and search API (`:3002`), and cleans up both on Ctrl+C.

## Creating a New Project

```bash
./scripts/new-project.sh my-docs "My Documentation"
```

This scaffolds:

```
my-docs/
├── docs.json           # Mintlify config with papermint theme
├── index.mdx           # Landing page
└── en/
    └── example.mdx     # Starter doc page
```

Then start it:

```bash
./start.sh              # will auto-detect and offer your new project
```

### Adding pages

1. Create `my-docs/en/my-page.mdx`:

   ```mdx
   ---
   title: My Page
   description: What this page covers
   ---

   # My Page

   Your content here. Use Mintlify components freely:

   <Note>These are globally available — no imports needed.</Note>
   ```

2. Add it to navigation in `my-docs/docs.json`:

   ```json
   {
     "group": "Guides",
     "pages": ["en/example", "en/my-page"]
   }
   ```

Available components: `<Note>`, `<Warning>`, `<Tip>`, `<Steps>`, `<Step>`, `<Tabs>`, `<Tab>`, `<CardGroup>`, `<Card>`

## Search & RAG

Each project gets a search API that works at two levels:

- **Keyword search** — always available, no external dependencies
- **Semantic search + RAG** *(optional)* — requires Ollama running locally

### How it works

1. All `.md`/`.mdx` files in the project directory are loaded and chunked (~500 tokens, split on headings)
2. **Without Ollama:** search uses keyword matching (substring density scoring) — works immediately
3. **With Ollama:** chunks are embedded via `bge-m3` and cached to disk (`search/.cache/<project>/`). Search combines keyword (40%) + semantic similarity (60%). The `/api/ask` endpoint streams RAG answers via `qwen2.5:7b-instruct`
4. On subsequent starts, if no docs changed, embeddings load from cache instantly
5. If Ollama goes down mid-session, search gracefully falls back to keyword-only

### API endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/search` | POST | Keyword search (+ semantic when Ollama available). Body: `{"query": "..."}` |
| `/api/ask` | POST | RAG Q&A with SSE streaming (requires Ollama). Body: `{"query": "..."}` |
| `/api/reindex` | POST | Re-index all docs (invalidates cache if content changed) |
| `/api/health` | GET | Health check, chunk count, and whether semantic search is active |

### Configuration

| Variable | Default | Description |
|---|---|---|
| `PROJECT` | `claude-code` | Which project directory to index |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama API URL |
| `EMBED_MODEL` | `bge-m3` | Embedding model |
| `CHAT_MODEL` | `qwen2.5:7b-instruct` | Chat model for RAG |
| `PORT` | `3002` | Search API port |

## Project Structure

```
papermint/
├── start.sh                # Launch everything for a project
├── flake.nix               # Nix dev shell (NixOS)
├── scripts/
│   └── new-project.sh      # Scaffold a new project
├── search/                 # Shared search + RAG API
│   ├── server.py
│   ├── indexer.py
│   └── sync-docs.py        # Import external markdown into a project
├── claude-code/            # Example project
│   ├── docs.json
│   ├── index.mdx
│   ├── custom.js           # Search modal (Cmd+K)
│   └── en/*.mdx
└── <your-project>/         # Your projects go here
```

## Syncing External Docs

Pull markdown files from an external directory into a Mintlify project:

```bash
cd search
python sync-docs.py /path/to/markdown --project my-docs
```

This copies `.md` files, converts them to `.mdx` with frontmatter, and updates navigation in `docs.json`.

## Theme

All projects share the papermint default theme:

- Dark mode by default
- Background: `#09090B` (dark), `#FDFDF7` (light)
- Primary: `#0E0E0E`, Accent: `#D4A27F`

Override per-project in `docs.json`.
