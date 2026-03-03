"""Document indexer: load, chunk, embed, and search docs via Ollama."""

import hashlib
import json
import os
import re
from pathlib import Path

import httpx
import numpy as np

# --- Cache directory ---
_CACHE_ROOT = Path(__file__).parent / ".cache"


def load_docs(docs_dir: str) -> list[dict]:
    """Read .md/.mdx files from docs_dir, strip frontmatter, return list of
    {path, title, content}."""
    docs = []
    docs_path = Path(docs_dir)
    for ext in ("*.md", "*.mdx"):
        for filepath in sorted(docs_path.rglob(ext)):
            text = filepath.read_text(encoding="utf-8")
            # Strip YAML frontmatter
            text = re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=re.DOTALL)
            # Derive title from first heading or filename
            title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
            title = title_match.group(1) if title_match else filepath.stem.replace("-", " ").title()
            rel = filepath.relative_to(docs_path)
            docs.append({
                "path": str(rel),
                "title": title,
                "content": text.strip(),
            })
    return docs


def _fingerprint(docs: list[dict], model: str) -> str:
    """SHA-256 of all doc contents + model name. Changes when any doc or model changes."""
    h = hashlib.sha256()
    for doc in sorted(docs, key=lambda d: d["path"]):
        h.update(doc["path"].encode())
        h.update(doc["content"].encode())
    h.update(model.encode())
    return h.hexdigest()


def _cache_dir(project: str) -> Path:
    d = _CACHE_ROOT / project
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_cache(project: str, fingerprint: str) -> tuple[list[dict], np.ndarray] | None:
    """Load cached chunks + embeddings if fingerprint matches. Returns None on miss."""
    d = _cache_dir(project)
    fp_file = d / "fingerprint"
    chunks_file = d / "chunks.json"
    emb_file = d / "embeddings.npy"

    if not all(f.exists() for f in (fp_file, chunks_file, emb_file)):
        return None

    if fp_file.read_text().strip() != fingerprint:
        return None

    chunks = json.loads(chunks_file.read_text(encoding="utf-8"))
    embeddings = np.load(emb_file)
    return chunks, embeddings


def save_cache(project: str, fingerprint: str, chunks: list[dict], embeddings: np.ndarray):
    """Persist chunks + embeddings to disk."""
    d = _cache_dir(project)
    (d / "fingerprint").write_text(fingerprint)
    (d / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    np.save(d / "embeddings.npy", embeddings)


def chunk_text(text: str, max_tokens: int = 500, overlap: int = 50) -> list[str]:
    """Split text into chunks of roughly max_tokens words with overlap.
    Splits on headings first, then paragraphs, then by word count."""
    # Split on markdown headings (##, ###, etc.)
    heading_pattern = re.compile(r"(?=^#{1,4}\s)", re.MULTILINE)
    sections = heading_pattern.split(text)
    sections = [s.strip() for s in sections if s.strip()]

    chunks = []
    for section in sections:
        words = section.split()
        if len(words) <= max_tokens:
            chunks.append(section)
        else:
            # Split long sections into overlapping windows
            start = 0
            while start < len(words):
                end = start + max_tokens
                chunk = " ".join(words[start:end])
                chunks.append(chunk)
                start = end - overlap
    return chunks


def build_index(docs: list[dict]) -> list[dict]:
    """Chunk all docs into a flat list of {doc_path, doc_title, text, heading}."""
    index = []
    for doc in docs:
        chunks = chunk_text(doc["content"])
        for chunk in chunks:
            # Try to extract heading from chunk start
            heading_match = re.match(r"^(#{1,4})\s+(.+)$", chunk, re.MULTILINE)
            heading = heading_match.group(2) if heading_match else ""
            index.append({
                "doc_path": doc["path"],
                "doc_title": doc["title"],
                "text": chunk,
                "heading": heading,
            })
    return index


async def embed_chunks(
    chunks: list[dict],
    ollama_url: str,
    model: str,
) -> np.ndarray:
    """Call Ollama /api/embed for each chunk, return (n_chunks, dim) array."""
    texts = [c["text"] for c in chunks]
    if not texts:
        return np.array([])

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Ollama /api/embed accepts a list of inputs
        resp = await client.post(
            f"{ollama_url}/api/embed",
            json={"model": model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()

    embeddings = np.array(data["embeddings"], dtype=np.float32)
    # Normalize for cosine similarity
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    embeddings = embeddings / norms
    return embeddings


async def embed_query(
    query: str,
    ollama_url: str,
    model: str,
) -> np.ndarray:
    """Embed a single query string."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{ollama_url}/api/embed",
            json={"model": model, "input": [query]},
        )
        resp.raise_for_status()
        data = resp.json()
    vec = np.array(data["embeddings"][0], dtype=np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def search_semantic(
    query_embedding: np.ndarray,
    chunk_embeddings: np.ndarray,
    top_k: int = 5,
) -> list[tuple[int, float]]:
    """Return top_k (index, score) pairs by cosine similarity."""
    if chunk_embeddings.size == 0:
        return []
    scores = chunk_embeddings @ query_embedding
    top_indices = np.argsort(scores)[::-1][:top_k]
    return [(int(i), float(scores[i])) for i in top_indices]


def search_keyword(
    query: str,
    chunks: list[dict],
    top_k: int = 5,
) -> list[tuple[int, float]]:
    """Simple case-insensitive substring match, scored by match density."""
    query_lower = query.lower()
    terms = query_lower.split()
    scored = []
    for i, chunk in enumerate(chunks):
        text_lower = chunk["text"].lower()
        matches = sum(1 for term in terms if term in text_lower)
        if matches > 0:
            density = matches / len(terms)
            scored.append((i, density))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
