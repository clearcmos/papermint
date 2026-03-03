# Docs Search API

Local search + RAG for Mintlify doc projects, powered by Ollama.

## Prerequisites

```bash
ollama pull bge-m3
ollama pull qwen2.5:7b-instruct
```

## Run

```bash
pip install -r requirements.txt
PROJECT=claude-code python server.py
```

Starts on `http://localhost:3002`. Indexes all `.md`/`.mdx` files in the project directory at startup.

## Sync NixOS Docs

Generate a Mintlify project from `/etc/nixos/docs`:

```bash
python sync-docs.py                              # defaults to nixos project
python sync-docs.py /path/to/docs --project foo  # custom source + project
```

## Endpoints

```bash
# Search
curl -s localhost:3002/api/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "hooks configuration"}'

# Ask (streamed)
curl -N localhost:3002/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"query": "how do hooks work?"}'

# Re-index without restart
curl -X POST localhost:3002/api/reindex

# Health check
curl localhost:3002/api/health
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PROJECT` | `claude-code` | Project directory to index (relative to repo root) |
| `DOCS_DIR` | `../<PROJECT>/` | Override: absolute path to docs directory |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama API URL |
| `EMBED_MODEL` | `bge-m3` | Embedding model name |
| `CHAT_MODEL` | `qwen2.5:7b-instruct` | Chat/RAG model name |
| `PORT` | `3002` | Search API port |
