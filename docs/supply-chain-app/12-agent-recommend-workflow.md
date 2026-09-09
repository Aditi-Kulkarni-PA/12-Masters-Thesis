# Recommendation Agent — Workflow, RAG Pipeline & Design

## Recommend Actions Agent

## Flow Diagram

```perl
┌─────────────────────────────────────────────────────────┐
│              Master Orchestrator Agent                  │
│                                                         │
│  Pre-requisite check (from [SYSTEM:] tag in query):     │
│  • FRESH → call recommendation_tool directly            │
│  • NOT FRESH → predict → diagnose → then recommend      │
└──────────────────────┬──────────────────────────────────┘
                       │ calls recommendation_tool
                       ▼
┌─────────────────────────────────────────────────────────┐
│           Recommendation Agent : subagent               │
│  Prompt: config/prompts/agents/recommendation.md        │
│  Output: RecommendedActionsList                         │
│    └─ RecommendedAction:                                │
│         action, action_desc, category, dimension,       │
│         supporting_data, sla_reference  ← NEW FIELD     │
│  tools=[recommend_actions]                              │
│  tool_choice="required" → MUST call the tool            │
└──────────────────────┬──────────────────────────────────┘
                       │ calls recommend_actions()
                       ▼
┌─────────────────────────────────────────────────────────┐
│         recommend_actions() — @tool            │
│         tools/recommend_actions.py                      │
│                                                         │
│  1. Validate prerequisites (DB + CSV exist)             │
│  2. Query SQLite: overall stats (hist_ + daily_)        │
│  3. Compare 6 dimensions (daily vs historical)          │
│  4. Get high-risk patterns (hist + daily, top 10)       │
│  5. Get worst dimensions (hist top 3 + daily top 3)     │
│  6. Read CSV for severity hotspots                      │
│  7. Build structured Markdown (9 sections)              │
│  8. ──► RAG: retrieve_sla_context(tool_output) ◄──      │
│         (lazy import, auto-appended)                    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│        retrieve_sla_context() — rag_knowledge.py        │
│                                                         │
│  1. _summarize_query(): extract key lines with          │
│     %, bold, risk, critical markers → focused query     │
│  2. Embed query (text-embedding-3-small)                │
│  3. ChromaDB vector search (TOP_K=15, cosine)           │
│     Auto-builds vectorstore if missing/stale            │
│  4.a _hybrid_prefilter(): 70% cosine sim                │
|                         + 30% keyword overlap           │
│                         → TOP_N=12 best chunks          │
│  4.b _reranker(): Cross-encoder rerank → TOP_8 chunks   │
│  5. Format as "--- SLA Knowledge Context ---" block     │
└────────────────────────┬────────────────────────────────┘
                         │ returns combined string:
                         │   [Data sections] + [SLA Context]
                         ▼
┌─────────────────────────────────────────────────────────┐
│      Recommendation Agent receives tool output          │
│                                                         │
│  Prompt mandates for EACH recommendation:               │
│  • supporting_data → cite specific data metrics         │
│  • sla_reference → MUST quote SLA clause/target/penalty │
│  • action_desc → explain gap + corrective action        │
│                                                         │
│  Produces RecommendedActionsList                        │
└──────────────────────┬──────────────────────────────────┘
                       │ returns to Master
                       ▼
┌─────────────────────────────────────────────────────────┐
│  App builds display from rows (post_processing.py)      │
│  summary_type: recommendation                           │
│  Renders: Quick-Win / Short-term / Long-term sections   │
│  Each action shows: data metrics + SLA reference        │
│  → MasterOutput.recommendation_summary                  │
└─────────────────────────────────────────────────────────┘
```

### Key Aspects to Remember

| Aspect | Detail |
|---|---|
| RAG is embedded in the tool | `retrieve_sla_context()` is called inside `recommend_actions()` via lazy import — the agent does **NOT** call RAG separately |
| Lazy import | `from tools.rag_knowledge import retrieve_sla_context` is inside the function body to avoid circular imports through `tools/__init__.py` |
| Prerequisites | The tool checks that the SQLite DB and daily delayed CSV exist (created by **predict + diagnose** steps) before proceeding |
| 6 categorical dimensions | `region`, `weather_condition`, `delivery_partner`, `delivery_mode`, `vehicle_type`, `distance_bucket` |
| Vectorstore auto-rebuild | ChromaDB at `vectorstore/` auto-rebuilds when the SLA file hash changes — no manual rebuild needed |
| Chunking | LangChain two-stage: `MarkdownHeaderTextSplitter (#/##/###)` → `RecursiveCharacterTextSplitter (2000 chars, 200 overlap)` → **73 chunks** |
| Re-ranking formula | `0.7 × cosine_similarity + 0.3 × keyword_overlap` — prioritizes semantic match but rewards keyword hits |
| Query summarization | Extracts lines with `%`, `**bold**`, `risk`, `critical`, `worse` markers — creates a focused query from the data output |
| `tool_choice="required"` | Agent **MUST** call `recommend_actions` — it cannot skip the tool and hallucinate recommendations |
| Output model | `RecommendedActionsList` → list of `RecommendedAction` with `category` (`long-term` / `short-term` / `quick-win`) and dimension fields |
| Graceful RAG failure | If RAG fails, the tool still returns the data sections with an error note — recommendations can still be generated from data alone |
