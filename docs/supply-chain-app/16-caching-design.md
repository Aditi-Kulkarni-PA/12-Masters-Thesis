# Caching Design

The system has four independent caching mechanisms serving different purposes: **sidecar-based freshness detection** (avoids redundant MCP tool calls across agent runs), a **ChromaDB embedding cache** (avoids re-embedding SLA document chunks on every app start), and an **in-process RAG retrieval cache** (avoids redundant embedding and ChromaDB calls within a session).

---

## 1. Sidecar Freshness Detection

### Purpose

The orchestration layer uses sidecar JSON files to detect whether expensive upstream pipeline steps (predict, diagnose) have already run recently. This prevents the Master Orchestrator from re-invoking `predict_delivery_delays` or `get_delay_diagnosis` on every query within the same session, saving both LLM tokens and ML inference cost.

### How It Works

```
predict_delivery_delays tool runs
        │
        └── writes daily_delivery_delay_prediction_meta.json
                          (sidecar for prediction freshness)

get_delay_diagnosis tool runs
        │
        └── writes diagnosis_meta.json
                          (sidecar for diagnosis freshness)
```

On every new user message, `build_freshness_system_msg()` in `app_utils.py` checks the mtime of both sidecar files against a 1-hour TTL:

```python
FRESHNESS_TTL = 3600  # seconds

def is_file_fresh(path: Path, ttl: int = FRESHNESS_TTL) -> bool:
    return path.is_file() and (time.time() - path.stat().st_mtime) < ttl
```

The result is appended to the user message as a `[SYSTEM: ...]` tag before it reaches the agent:

| State | SYSTEM tag injected |
|---|---|
| Both fresh | `FRESH — all tools can proceed without re-running predict or diagnose` |
| Prediction fresh, diagnosis stale | `Predict is FRESH. Diagnose NOT FRESH — run diagnose before recommend.` |
| Prediction stale | `NOT FRESH — run predict first as prerequisite for all other tools.` |

### Freshness in the Action Plan

`build_action_plan()` also reads sidecar freshness to annotate each plan step before showing the confirmation prompt to the user:

```
TOOL_DEPS["recommend"] = [
    "Predict delivery delays (if not fresh)",
    "Diagnose delay patterns (if not fresh)",
    "Generate optimization recommendations"
]
```

`_resolve_freshness()` converts `(if not fresh)` steps to either the base step label (run it) or `{step} -- skip (data is fresh)` — so the user sees exactly which steps will execute and which will be skipped before confirming.

### Sidecar File Locations

| Sidecar | Path | Written by | Checked by |
|---|---|---|---|
| Prediction | `supply_chain_delivery_app/output/daily_delivery_delay_prediction_meta.json` | `daily_predict.py` (MCP tool) | `build_freshness_system_msg()`, `build_action_plan()` |
| Diagnosis | `supply_chain_delivery_app/output/diagnosis_meta.json` | `save_diagnosis_sidecar()` in `app_utils.py` | same |

The diagnosis sidecar stores only the first 500 characters of the diagnosis summary — enough to detect freshness without storing the full output.

### Why File mtime Rather Than In-Memory State?

The Gradio app runs as a long-lived process. Using file mtime means:
- Freshness survives page refreshes (Gradio state resets on reconnect; files persist)
- Freshness is readable by external tools and scripts without connecting to the running app
- Sidecar files double as audit records (see `17-observability-logging.md`)

---

## 2. RAG Retrieval Cache

### Purpose

Each call to `retrieve_sla_context()` involves an OpenAI embedding API call, a ChromaDB query, hybrid scoring, and cross-encoder inference. When the same worsening-dimension summary is queried multiple times in a session, the retrieval result — the SLA policy chunks returned to the Recommend agent — will be identical. The cache avoids repeating all of this work.

**Why cache at retrieval level rather than response level?** A response cache would skip both retrieval and LLM inference, but it would incorrectly serve stale recommendations when the underlying SQLite data changes between runs. The Recommend agent combines two inputs: the SLA chunks (stable across the session) and live SQLite statistics (today's delay rates, which change every prediction run). Caching at retrieval level avoids redundant vector search cost while still allowing the LLM to synthesise fresh recommendations from live data on every call.

### Cache Key

```python
def _retrieval_cache_key(query: str) -> str:
    sla_hash = _file_hash(_SLA_FILE)   # SHA-256[:16] of source SLA file
    raw = f"{sla_hash}|{query}|{_EMBED_MODEL}|{_TOP_K}|{_RERANK_TOP_N}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

The SLA file hash component means the cache **auto-invalidates** if the knowledge source changes mid-session — no manual flush needed. The embed model, top-K, and rerank-N params are included so changing retrieval config also invalidates stale entries.

### Eviction Policy

```python
_RETRIEVAL_CACHE_MAX_SIZE = 200

if len(_retrieval_cache) >= _RETRIEVAL_CACHE_MAX_SIZE:
    _retrieval_cache.clear()   # full eviction, not LRU
```

Full clear (not LRU) is used because the cache is session-scoped and the 200-entry ceiling is unlikely to be reached in normal use. Simplicity over precision.

### Cache Scope

The retrieval cache is a module-level dict — it lives for the lifetime of the Python process. It does **not** persist across app restarts. The ChromaDB store (a separate, non-cache mechanism) handles cross-session persistence of the vector index itself.

---

## 3. ChromaDB Embedding Cache

### Purpose

The ChromaDB `PersistentClient` stores the 1536-dim embeddings for all SLA document chunks to disk (`supply_chain_delivery_app/vectorstore/`). On every app start, these embeddings are loaded from disk rather than recomputed — avoiding repeated OpenAI embedding API calls and 5–10 seconds of startup latency.

### How It Works

Rebuild is triggered only when the SLA source file changes, detected by comparing a stored SHA-256 hash:

```python
hash_file = _CHROMA_DIR / ".source_hash"
current_hash = _file_hash(_SLA_FILE)

if hash_file.exists() and hash_file.read_text().strip() == current_hash:
    # collection exists and is non-empty → load from disk, skip rebuild
else:
    # embed all chunks via OpenAI API → write to disk → save new hash
```

### What Gets Cached

All SLA document chunk embeddings produced during the initial build:
- Source: `delivery_sla_github_ready.md` (36 sections)
- Chunks: ~500-token segments with header breadcrumbs prepended
- Embeddings: `text-embedding-3-small` (1536-dim, OpenAI)

### Why This Approach

Embedding all SLA chunks via the OpenAI API is a one-time cost that should not recur on every app restart. Hash-based invalidation ensures the store is always in sync with the source document — if the SLA is updated, the store rebuilds automatically on next startup with no manual step required.

See [`docs/07-vectorstore-rag-design.md`](07-vectorstore-rag-design.md) for the full chunking and embedding pipeline.

---

## 4. Response Cache

### Purpose

A single full pipeline run is expensive: MCP tool invocations trigger ML inference and 27-table SQLite writes; the agent chain calls multiple LLMs sequentially; RAG retrieval adds an OpenAI embedding call plus vector search and cross-encoder inference. If the user re-submits the same query and no upstream data has changed, repeating all of this work produces identical outputs. The response cache returns the stored tab outputs instantly in this case.

### Cache Key

```python
def _response_cache_key(message, orders_path, predict_sidecar, diag_sidecar):
    model = os.getenv("OPENAI_MODEL", "")
    orders_hash = _file_hash(orders_path) if orders_path else "none"
    predict_mtime = str(predict_sidecar.stat().st_mtime) if predict_sidecar.exists() else "missing"
    diag_mtime    = str(diag_sidecar.stat().st_mtime)    if diag_sidecar.exists()    else "missing"
    raw = f"{message.strip()}|{orders_hash}|{predict_mtime}|{diag_mtime}|{model}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

The key includes:
- Normalised user message — same query must match exactly
- Orders CSV file hash — new input data invalidates the cache
- Predict sidecar mtime — a new prediction run invalidates the cache
- Diagnosis sidecar mtime — a new diagnosis run invalidates the cache
- Model name — model change invalidates the cache

### What Gets Cached

All tab outputs produced by the agent run: predict DataFrame + markdown, diagnosis tables, simulation DataFrame + markdown, recommendation markdown, email markdown + CSV. Stored as a dict keyed by tab name; DataFrames are `.copy()`-ed to avoid mutation.

### Eviction Policy

```python
_RESPONSE_CACHE_MAX_SIZE = 50

if len(_response_cache) >= _RESPONSE_CACHE_MAX_SIZE:
    _response_cache.clear()   # full eviction
```

50-entry ceiling (smaller than retrieval cache — full pipeline outputs are larger objects). Full eviction on ceiling, same rationale as retrieval cache.

### Why Not a Response Cache at the RAG Level?

The RAG retrieval cache (Section 2) caches only the SLA chunks returned to the Recommend agent, not the final recommendation. This is intentional: the Recommend agent synthesises those chunks with live SQLite statistics, so the recommendation text varies even when the SLA chunks are identical. The response cache (this section) operates at the full pipeline level and correctly invalidates when SQLite data changes (via sidecar mtime), making it safe to cache the complete output including recommendations.

---

## Summary

| Mechanism | Scope | Invalidation | Purpose |
|---|---|---|---|
| Sidecar freshness detection | Per-run (file-based) | 1-hour TTL on file mtime | Skip redundant predict/diagnose MCP tool calls |
| ChromaDB embedding cache | Persistent (file-based) | SLA file SHA-256 hash change | Skip re-embedding SLA chunks via OpenAI API on every app start |
| RAG retrieval cache | Per-session (in-memory) | SLA file hash change; 200-entry ceiling | Skip redundant query embedding + ChromaDB query + cross-encoder calls |
| Response cache | Per-session (in-memory) | Orders file hash, sidecar mtimes, model change; 50-entry ceiling | Skip full pipeline re-run when input data and sidecars are unchanged |
