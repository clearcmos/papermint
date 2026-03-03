"""FastAPI search service for self-hosted Mintlify docs with Ollama."""

import json
import os
from contextlib import asynccontextmanager

import httpx
import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from indexer import (
    _fingerprint,
    build_index,
    embed_chunks,
    embed_query,
    load_cache,
    load_docs,
    save_cache,
    search_keyword,
    search_semantic,
)

# --- Config ---
PROJECT = os.environ.get("PROJECT", "claude-code")
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.environ.get("DOCS_DIR", os.path.join(_repo_root, PROJECT))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "qwen2.5:7b-instruct")
PORT = int(os.environ.get("PORT", "3002"))

# --- In-memory state ---
chunks: list[dict] = []
chunk_embeddings: np.ndarray = np.array([])


async def do_index():
    """Load docs, chunk, and embed. Uses disk cache when docs haven't changed.
    Falls back to keyword-only search if Ollama is unavailable."""
    global chunks, chunk_embeddings
    docs = load_docs(DOCS_DIR)
    fp = _fingerprint(docs, EMBED_MODEL)

    cached = load_cache(PROJECT, fp)
    if cached:
        chunks, chunk_embeddings = cached
        print(f"Cache hit — loaded {len(chunks)} chunks from disk")
        return len(chunks)

    chunks = build_index(docs)
    if chunks:
        try:
            print("Cache miss — embedding via Ollama...")
            chunk_embeddings = await embed_chunks(chunks, OLLAMA_URL, EMBED_MODEL)
            save_cache(PROJECT, fp, chunks, chunk_embeddings)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
            print(f"Ollama unavailable ({e}) — keyword search only")
            chunk_embeddings = np.array([])
    else:
        chunk_embeddings = np.array([])
    return len(chunks)


@asynccontextmanager
async def lifespan(app: FastAPI):
    n = await do_index()
    print(f"Indexed {n} chunks from {DOCS_DIR}")
    yield


app = FastAPI(title="Docs Search", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    query: str


def _slug_to_url(doc_path: str) -> str:
    """Convert a doc path like 'en/hooks.mdx' to '/en/hooks', 'index.mdx' to '/'."""
    slug = doc_path.rsplit(".", 1)[0]  # strip extension
    if slug == "index":
        return "/"
    return f"/{slug}"


def _snippet(text: str, query: str, length: int = 200) -> str:
    """Extract a snippet around the first occurrence of query terms."""
    lower = text.lower()
    terms = query.lower().split()
    best_pos = len(text)
    for term in terms:
        pos = lower.find(term)
        if 0 <= pos < best_pos:
            best_pos = pos
    start = max(0, best_pos - length // 4)
    end = start + length
    snippet = text[start:end].replace("\n", " ").strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


@app.post("/api/search")
async def search(req: QueryRequest):
    """Hybrid keyword + semantic search, returns top results."""
    query = req.query.strip()
    if not query:
        return {"results": []}

    # Keyword search (always available)
    kw_results = search_keyword(query, chunks, top_k=10)

    # Semantic search (best-effort — skipped if Ollama is down)
    sem_results = []
    if chunk_embeddings.size > 0:
        try:
            q_emb = await embed_query(query, OLLAMA_URL, EMBED_MODEL)
            sem_results = search_semantic(q_emb, chunk_embeddings, top_k=10)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
            pass

    # Merge: keyword-only when no semantic results, hybrid when both available
    scores: dict[int, float] = {}
    if sem_results:
        for idx, score in kw_results:
            scores[idx] = scores.get(idx, 0) + score * 0.4
        for idx, score in sem_results:
            scores[idx] = scores.get(idx, 0) + score * 0.6
    else:
        for idx, score in kw_results:
            scores[idx] = score

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:8]

    # Deduplicate by page
    seen_pages = set()
    results = []
    for idx, score in ranked:
        chunk = chunks[idx]
        page = chunk["doc_path"]
        if page in seen_pages:
            continue
        seen_pages.add(page)
        results.append({
            "title": chunk["doc_title"],
            "heading": chunk["heading"],
            "url": _slug_to_url(page),
            "snippet": _snippet(chunk["text"], query),
            "score": round(score, 4),
        })

    return {"results": results}


@app.post("/api/ask")
async def ask(req: QueryRequest):
    """RAG: retrieve top chunks, stream answer from Ollama chat."""
    query = req.query.strip()
    if not query:
        return {"answer": ""}

    # Retrieve context via semantic search (fall back to keyword if Ollama down)
    context_chunks = []
    try:
        if chunk_embeddings.size > 0:
            q_emb = await embed_query(query, OLLAMA_URL, EMBED_MODEL)
            top = search_semantic(q_emb, chunk_embeddings, top_k=5)
            context_chunks = [chunks[i]["text"] for i, _ in top]
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
        pass

    if not context_chunks:
        kw_top = search_keyword(query, chunks, top_k=5)
        context_chunks = [chunks[i]["text"] for i, _ in kw_top]

    context = "\n\n---\n\n".join(context_chunks)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful documentation assistant. Answer the user's question "
                "based on the provided documentation context. Be concise and accurate. "
                "If the context doesn't contain enough information, say so.\n\n"
                f"Documentation context:\n{context}"
            ),
        },
        {"role": "user", "content": query},
    ]

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_URL}/api/chat",
                    json={"model": CHAT_MODEL, "messages": messages, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        token = data.get("message", {}).get("content", "")
                        if token:
                            yield f"data: {json.dumps({'token': token})}\n\n"
                        if data.get("done"):
                            yield "data: [DONE]\n\n"
                            break
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
            yield f"data: {json.dumps({'token': 'AI is currently unavailable. Please try again later.'})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/reindex")
async def reindex():
    """Re-index all docs."""
    n = await do_index()
    return {"status": "ok", "chunks": n}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "chunks": len(chunks),
        "semantic": chunk_embeddings.size > 0,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=True)
