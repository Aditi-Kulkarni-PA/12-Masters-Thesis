# Vector Store & RAG Pipeline Design

## Overview

The RAG pipeline provides the Recommend agent with grounded SLA/OLA policy context at query time. Rather than pre-loading the entire SLA document into the prompt, relevant sections are retrieved dynamically based on what's actually failing today — making recommendations specific to the current delay patterns rather than generic.

```
recommend_actions tool output (worsening dims, high-risk patterns)
        │
        ▼
┌───────────────────────────────┐
│  Query Summarisation          │  Extract key stats, dimension names,
│  (_summarize_query)           │  risk levels from raw tool output
└───────────────┬───────────────┘
                │ focused query string
                ▼
┌───────────────────────────────┐
│  Stage 1: ChromaDB            │  Embed query → cosine similarity
│  Broad Retrieval              │  top-K = 15 chunks returned
└───────────────┬───────────────┘
                │ top-15 chunks + distances
                ▼
┌───────────────────────────────┐
│  Stage 2: Hybrid Pre-filter   │  0.7 × cosine + 0.3 × keyword overlap
│  (_hybrid_prefilter)          │  top-15 → top-12
└───────────────┬───────────────┘
                │ top-12 candidates
                ▼
┌───────────────────────────────┐
│  Stage 3: Cross-encoder       │  cross-encoder/ms-marco-MiniLM-L-6-v2
│  Rerank (_cross_encoder_rerank│  Scores (query, doc) pairs jointly
│  )                            │  top-12 → top-8
└───────────────┬───────────────┘
                │ top-8 SLA chunks
                ▼
        Appended to tool output between
        "--- SLA Knowledge Context ---" delimiters
        → passed to Recommend agent prompt
```

## Knowledge Source

| Property | Value |
|---|---|
| File | `supply_chain_delivery_app/knowledge/delivery_sla_github_ready.md` |
| Type | Custom-authored SLA/OLA policy document |
| Sections | 36 sections covering performance targets, penalty thresholds, escalation tiers, partner benchmarks, weather policies, distance guidelines, improvement priorities |
| Justification | No publicly available SLA document exists at the required clause granularity. Custom-authored to ensure RAG context contains specific penalty amounts, escalation tiers, and improvement priorities that ground each recommendation. |

## ChromaDB Store

| Property | Value |
|---|---|
| Location | `supply_chain_delivery_app/vectorstore/` (gitignored) |
| Client | `chromadb.PersistentClient` |
| Collection | `sla_knowledge` |
| Distance metric | Cosine (`hnsw:space: cosine`) |
| Embedding model | `text-embedding-3-small` (1536-dim, OpenAI) |
| Rebuild trigger | SHA-256 hash of source SLA file stored in `vectorstore/.source_hash`; mismatch triggers full rebuild |

The store is built once and reused across sessions. It auto-rebuilds only if the SLA file changes — no manual step required.

## Chunking Pipeline

Two-stage langchain pipeline applied to the SLA markdown:

**Stage 1 — `MarkdownHeaderTextSplitter`**  
Splits on `#`, `##`, `###` headings. Preserves header hierarchy as metadata per chunk. This keeps SLA section context intact (e.g. a chunk knows it belongs to `Section 3.2 Express Delivery Targets`).

**Stage 2 — `RecursiveCharacterTextSplitter`**  
Further splits oversized sections. Chunk target: 500 tokens (~2,000 chars). Overlap: 200 chars (~50 tokens) between consecutive chunks to prevent context loss at boundaries.

**Header breadcrumb prepended to each chunk:**  
```
[SLA/OLA Policy > Performance Targets > Express Delivery]
<chunk content>
```
This ensures the embedding carries section-level context, not just the raw paragraph text.

## Three-Stage Retrieval

### Stage 1 — Broad ChromaDB Retrieval (top-15)

The raw `recommend_actions` tool output is first summarised by `_summarize_query()` before embedding. The summariser extracts lines containing `##`, `**`, `%`, `critical`, `high`, `delay_rate`, or `worse` — the signal-carrying lines — and caps at 40 lines / 2,000 chars. This avoids embedding noise and works within token limits.

The summarised query is then embedded and queried against ChromaDB. Returns top-15 chunks by cosine similarity.

### Stage 2 — Hybrid Pre-filter (top-15 → top-12)

Cosine similarity alone can miss exact terminology matches (e.g. a specific SLA clause number). The hybrid scorer adds a keyword overlap term:

```
combined_score = 0.7 × cosine_similarity + 0.3 × keyword_overlap_ratio

where:
  cosine_similarity = 1 - chroma_distance
  keyword_overlap   = |query_tokens ∩ doc_tokens| / |query_tokens|
```

Tokens are lowercased word-boundary splits. The 70/30 weighting keeps semantic similarity dominant while giving SLA-specific terminology a meaningful boost.

### Stage 3 — Cross-encoder Rerank (top-12 → top-8)

The cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`) scores each `(query, document)` pair **jointly** — both texts are processed together in a single forward pass rather than as independent embeddings. This makes the score sensitive to exact query-document relevance rather than just general semantic proximity.

Model is lazy-loaded on first call and cached as a module-level singleton for the session lifetime.

Final top-8 chunks are returned ordered by cross-encoder score and appended to the tool output.

## Retrieval Cache

An in-process dictionary cache (`_retrieval_cache`) avoids redundant embedding and ChromaDB calls within a session.

**Cache key** is a SHA-256 hash of:
```
{sla_file_hash} | {summarized_query} | {embed_model} | {top_K} | {rerank_top_N}
```

The SLA file hash component means the cache auto-invalidates if the knowledge source changes mid-session. Maximum 200 entries; full eviction (not LRU) when the limit is reached.

## Output Format

Retrieved chunks are formatted and injected into the `recommend_actions` tool output:

```
--- SLA Knowledge Context (retrieved via RAG) ---

### SLA Reference 1 (cross-encoder score: 0.8821)
[SLA/OLA Policy > Performance Targets > Express Delivery]
<chunk text>

### SLA Reference 2 (cross-encoder score: 0.7443)
...

--- End SLA Context ---
```

The Recommend agent's prompt instructs it to cite specific SLA clauses, penalty amounts, and escalation tiers found in this context block. Generic recommendations are structurally prevented — the prompt requires a non-empty `sla_reference` field quoting a specific clause for every recommendation.

## Key Design Decisions

**Why query summarisation before embedding?** The raw `recommend_actions` tool output can be several thousand characters. Embedding the full output wastes tokens, dilutes the signal with boilerplate, and risks hitting embedding model input limits. The summariser extracts only the statistically significant lines.

**Why hybrid scoring instead of cosine alone?** SLA documents contain specific terminology (clause numbers, threshold values, partner names) that a bi-encoder embedding may not distinguish from semantically similar but less relevant text. The 30% keyword term ensures exact matches on SLA-specific language score higher.

**Why cross-encoder as a third stage rather than first?** Cross-encoders are significantly slower than bi-encoder cosine similarity because they process query-document pairs jointly. Running it over the full collection would be impractical. Limiting it to the top-12 hybrid candidates gives near-optimal precision at low latency cost.

**Why persistent store rather than in-memory?** The SLA document is static for a given session. Rebuilding embeddings on every app start would add 5–10 seconds of startup latency and unnecessary OpenAI API calls. Persistence with file-hash invalidation gives the best of both: fast startup and automatic freshness.
