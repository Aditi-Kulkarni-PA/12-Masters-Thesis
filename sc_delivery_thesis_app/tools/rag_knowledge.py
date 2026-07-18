"""
RAG (Retrieval-Augmented Generation) module for supply chain SLA knowledge.

Best practices implemented:
- Markdown-aware chunking via langchain MarkdownHeaderTextSplitter + RecursiveCharacterTextSplitter
- Overlapping windows between chunks for context continuity
- OpenAI text-embedding-3-small for embeddings
- ChromaDB persistent vector store (rebuilds if source file changes)
- Query summarization: condenses long tool output into a focused query
- Two-stage re-ranking pipeline:
    Stage 1 — Hybrid pre-filter: 0.7 × cosine similarity + 0.3 × keyword overlap (top-K → top-8)
    Stage 2 — Cross-encoder rerank: cross-encoder/ms-marco-MiniLM-L-6-v2 (top-8 → top-N)
"""

import hashlib
import logging
import os
import re
import time
from pathlib import Path

import chromadb
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from openai import OpenAI
from sentence_transformers import CrossEncoder

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_APP_DIR = Path(__file__).resolve().parent.parent          # supply_chain_delivery_app/
_KNOWLEDGE_DIR = _APP_DIR / "knowledge"
_SLA_FILE = _KNOWLEDGE_DIR / "delivery_sla_github_ready.md"
_CHROMA_DIR = _APP_DIR / "vectorstore"                     # persistent chroma store

# ---------------------------------------------------------------------------
# Embedding config
# ---------------------------------------------------------------------------
_EMBED_MODEL = "text-embedding-3-small"  # 1536-dim embeddings

# Chunking params
_CHUNK_MAX_TOKENS = 500          # target tokens per chunk (approx 4 chars/token)
_CHUNK_MAX_CHARS = _CHUNK_MAX_TOKENS * 4
_OVERLAP_CHARS = 200             # ~50 token overlap between consecutive chunks

# Retrieval params
_TOP_K = 15                      # broad retrieval from chroma
_HYBRID_PRE_FILTER_N = 12        # hybrid stage narrows to this before cross-encoder
_RERANK_TOP_N = 8                # final cross-encoder results returned

# Cross-encoder model (downloaded once, cached locally by sentence-transformers)
_CROSS_ENCODER_MODEL = os.environ.get(
    "SC_CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
)
_cross_encoder: CrossEncoder | None = None

_COLLECTION_NAME = "sla_knowledge"
_LOGGER = logging.getLogger("supply_chain_delivery_app")


def _get_cross_encoder() -> CrossEncoder:
    """Lazy-load cross-encoder singleton (downloads model on first call)."""
    global _cross_encoder
    if _cross_encoder is None:
        _LOGGER.info("rag.cross_encoder.load model=%s", _CROSS_ENCODER_MODEL)
        _cross_encoder = CrossEncoder(_CROSS_ENCODER_MODEL)
        _LOGGER.info("rag.cross_encoder.ready")
    return _cross_encoder

# ---------------------------------------------------------------------------
# In-process retrieval cache
# Keyed on: SLA file hash | summarized query | embed model | top-K | rerank-N
# Invalidates automatically when the SLA source file changes.
# ---------------------------------------------------------------------------
_retrieval_cache: dict[str, str] = {}
_RETRIEVAL_CACHE_MAX_SIZE = 200

# ---------------------------------------------------------------------------
# Singleton client
# ---------------------------------------------------------------------------
_client: OpenAI | None = None


def _get_openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()  # reads OPENAI_API_KEY from env
    return _client


# ---------------------------------------------------------------------------
# Markdown-aware chunking (langchain two-stage pipeline)
# ---------------------------------------------------------------------------
# Stage 1: MarkdownHeaderTextSplitter – splits on #, ##, ### headings,
#           preserving header metadata for each section.
# Stage 2: RecursiveCharacterTextSplitter – further splits large sections
#           into sized chunks with overlap for context continuity.

_MD_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]
_md_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=_MD_HEADERS,
    strip_headers=False,
)
_char_splitter = RecursiveCharacterTextSplitter(
    chunk_size=_CHUNK_MAX_CHARS,
    chunk_overlap=_OVERLAP_CHARS,
)


def _chunk_document(text: str) -> list[str]:
    """Two-stage chunking pipeline:
    1. Split by markdown headers (preserves section hierarchy in metadata)
    2. Recursively split oversized sections with char overlap
    Returns plain strings with section context prepended."""
    header_docs = _md_splitter.split_text(text)
    chunks = _char_splitter.split_documents(header_docs)

    result: list[str] = []
    for doc in chunks:
        # Prepend header breadcrumb for retrieval context
        breadcrumb = " > ".join(doc.metadata.get(h, "") for _, h in _MD_HEADERS if doc.metadata.get(h))
        if breadcrumb:
            result.append(f"[{breadcrumb}]\n{doc.page_content}")
        else:
            result.append(doc.page_content)
    return result


# ---------------------------------------------------------------------------
# File-hash based cache invalidation
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _retrieval_cache_key(query: str) -> str:
    """Stable cache key for a retrieval result.
    Embeds the SLA source hash so any file change auto-invalidates."""
    sla_hash = _file_hash(_SLA_FILE) if _SLA_FILE.exists() else "missing"
    raw = f"{sla_hash}|{query}|{_EMBED_MODEL}|{_TOP_K}|{_RERANK_TOP_N}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Build / load ChromaDB collection
# ---------------------------------------------------------------------------

def _get_collection() -> chromadb.Collection:
    """Return the chroma collection, rebuilding if the source file changed."""
    started = time.perf_counter()
    _LOGGER.info("rag.collection.load.started")
    chroma_client = chromadb.PersistentClient(path=str(_CHROMA_DIR))

    # Check if we need to rebuild
    current_hash = _file_hash(_SLA_FILE) if _SLA_FILE.exists() else "missing"
    hash_file = _CHROMA_DIR / ".source_hash"

    needs_rebuild = True
    if hash_file.exists() and hash_file.read_text().strip() == current_hash:
        try:
            coll = chroma_client.get_collection(name=_COLLECTION_NAME)
            if coll.count() > 0:
                needs_rebuild = False
        except Exception:
            pass

    if needs_rebuild:
        _LOGGER.info("rag.collection.rebuild.started source=%s", _SLA_FILE)
        print(f"[RAG] Building vector store from {_SLA_FILE.name} ...", flush=True)
        # Delete old collection if it exists
        try:
            chroma_client.delete_collection(name=_COLLECTION_NAME)
        except Exception:
            pass

        coll = chroma_client.create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        text = _SLA_FILE.read_text(encoding="utf-8")
        chunks = _chunk_document(text)

        # Embed all chunks in batches
        batch_size = 50
        all_embeddings: list[list[float]] = []
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            resp = _get_openai().embeddings.create(model=_EMBED_MODEL, input=batch)
            all_embeddings.extend([d.embedding for d in resp.data])

        # Add to collection
        coll.add(
            ids=[f"chunk_{i}" for i in range(len(chunks))],
            embeddings=all_embeddings,
            documents=chunks,
            metadatas=[{"chunk_idx": i} for i in range(len(chunks))],
        )

        # Save hash
        _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        hash_file.write_text(current_hash)
        print(f"[RAG] Indexed {len(chunks)} chunks.", flush=True)
        _LOGGER.info("rag.collection.rebuild.completed chunks=%s", len(chunks))
    else:
        coll = chroma_client.get_collection(name=_COLLECTION_NAME)

    _LOGGER.info(
        "rag.collection.load.completed duration_ms=%s count=%s",
        int((time.perf_counter() - started) * 1000),
        coll.count(),
    )

    return coll


# ---------------------------------------------------------------------------
# Query summarization
# ---------------------------------------------------------------------------

def _summarize_query(raw_data: str, max_chars: int = 2000) -> str:
    """Condense a long tool output into a focused search query.
    Uses a simple heuristic: extract key statistics and dimension names."""
    # Extract lines with numbers and bold markers — these carry the signal
    key_lines: list[str] = []
    for line in raw_data.split("\n"):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        # Keep headers, lines with percentages, bold terms, or risk levels
        if any(marker in line_stripped for marker in ["##", "**", "***", "%", "critical", "high", "delay_rate", "worse"]):
            key_lines.append(line_stripped)

    summary = "\n".join(key_lines[:40])  # cap at 40 most relevant lines
    if len(summary) > max_chars:
        summary = summary[:max_chars]

    return summary


# ---------------------------------------------------------------------------
# Stage 1 — Hybrid pre-filter: cosine similarity + keyword overlap
# ---------------------------------------------------------------------------

def _hybrid_prefilter(query: str, documents: list[str], distances: list[float],
                      top_n: int = _HYBRID_PRE_FILTER_N) -> list[tuple[str, float]]:
    """Narrow top-K ChromaDB results to top-N using:
    1. Cosine similarity (1 - chroma distance)
    2. Keyword overlap ratio (query tokens ∩ doc tokens / query tokens)
    Combined score: 70% embedding similarity + 30% keyword overlap.
    """
    query_tokens = set(re.findall(r"\w+", query.lower()))
    scored: list[tuple[str, float]] = []

    for doc, dist in zip(documents, distances):
        sim = max(0.0, 1.0 - dist)
        doc_tokens = set(re.findall(r"\w+", doc.lower()))
        overlap = len(query_tokens & doc_tokens) / max(len(query_tokens), 1)
        combined = 0.7 * sim + 0.3 * overlap
        scored.append((doc, combined))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]


# ---------------------------------------------------------------------------
# Stage 2 — Cross-encoder rerank
# ---------------------------------------------------------------------------

def _cross_encoder_rerank(query: str, candidates: list[tuple[str, float]],
                           top_n: int = _RERANK_TOP_N) -> list[tuple[str, float]]:
    """Final rerank using cross-encoder/ms-marco-MiniLM-L-6-v2.
    Scores each (query, document) pair jointly — much more precise than
    bi-encoder cosine similarity because it attends to both texts together.
    Returns top-N by cross-encoder score.
    """
    if not candidates:
        return []
    docs = [doc for doc, _ in candidates]
    pairs = [(query, doc) for doc in docs]
    scores = _get_cross_encoder().predict(pairs)
    reranked = sorted(zip(docs, scores.tolist()), key=lambda x: x[1], reverse=True)
    return [(doc, float(score)) for doc, score in reranked[:top_n]]


# ---------------------------------------------------------------------------
# Public API: retrieve SLA knowledge for a recommendation query
# ---------------------------------------------------------------------------

def retrieve_sla_context(
    tool_output: str, rerank_top_n: int = _RERANK_TOP_N, query_override: str | None = None,
) -> str:
    """Given the raw output from recommend_actions(),
    retrieve the most relevant SLA knowledge chunks.

    Two-stage retrieval pipeline:
    1. Summarize the tool output into a focused query (or use query_override verbatim —
       lets callers, e.g. evals, retrieve for a specific topic/query directly)
    2. Embed the query (OpenAI text-embedding-3-small)
    3. Retrieve top-K candidates from ChromaDB (cosine similarity)
    4. Hybrid pre-filter: 0.7×cosine + 0.3×keyword overlap → top-8
    5. Cross-encoder rerank: cross-encoder/ms-marco-MiniLM-L-6-v2 → top-N
    6. Return concatenated top-N chunks as context string
    """
    started = time.perf_counter()
    _LOGGER.info("rag.retrieve.started input_len=%s rerank_top_n=%s", len(tool_output or ""), rerank_top_n)
    if not _SLA_FILE.exists():
        _LOGGER.warning("rag.retrieve.no_source_file path=%s", _SLA_FILE)
        return "[RAG] No SLA knowledge file found."

    # 1. Summarize (or use caller-supplied query verbatim)
    query = query_override.strip() if query_override else _summarize_query(tool_output)
    if not query.strip():
        query = "delivery SLA performance targets delay thresholds partner benchmarks"

    # 1b. Check retrieval cache before any embedding or chroma work
    _rkey = _retrieval_cache_key(query)
    if _rkey in _retrieval_cache:
        _LOGGER.info(
            "rag.retrieve.cache_hit duration_ms=%s",
            int((time.perf_counter() - started) * 1000),
        )
        return _retrieval_cache[_rkey]

    # 2. Embed
    resp = _get_openai().embeddings.create(model=_EMBED_MODEL, input=[query])
    query_embedding = resp.data[0].embedding

    # 3. Retrieve
    coll = _get_collection()
    results = coll.query(
        query_embeddings=[query_embedding],
        n_results=_TOP_K,
    )

    documents = results["documents"][0] if results["documents"] else []
    distances = results["distances"][0] if results["distances"] else []

    if not documents:
        _LOGGER.info("rag.retrieve.no_documents duration_ms=%s", int((time.perf_counter() - started) * 1000))
        return "[RAG] No relevant SLA chunks found."

    # 4. Hybrid pre-filter: cosine + keyword → top-8
    hybrid_candidates = _hybrid_prefilter(query, documents, distances, top_n=_HYBRID_PRE_FILTER_N)
    _LOGGER.info("rag.retrieve.hybrid_prefilter candidates=%s", len(hybrid_candidates))

    # 5. Cross-encoder final rerank: → top-N
    ranked = _cross_encoder_rerank(query, hybrid_candidates, top_n=rerank_top_n)
    _LOGGER.info("rag.retrieve.cross_encoder_rerank returned=%s", len(ranked))

    # 6. Format
    parts = [
        "--- SLA Knowledge Context (retrieved via RAG) ---",
    ]
    for i, (doc, score) in enumerate(ranked, 1):
        parts.append(f"\n### Retrieved Section {i}\n{doc}")

    parts.append("\n--- End SLA Context ---")
    _LOGGER.info(
        "rag.retrieve.completed duration_ms=%s retrieved=%s returned=%s",
        int((time.perf_counter() - started) * 1000),
        len(documents),
        len(ranked),
    )
    context_str = "\n".join(parts)

    # Store in retrieval cache; evict all entries if over the size cap
    if len(_retrieval_cache) >= _RETRIEVAL_CACHE_MAX_SIZE:
        _retrieval_cache.clear()
        _LOGGER.info("rag.retrieve.cache_evicted")
    _retrieval_cache[_rkey] = context_str
    return context_str
