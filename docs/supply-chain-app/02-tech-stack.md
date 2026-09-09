# Technology Stack

| Category | Technology / Library | Comment |
|---|---|---|
| LLM backbone | GPT-5.4, GPT-4.1-mini | Tested multiple models for intent classification and prompt execution (GPT-4.1-mini, GPT-5.1, GPT-5.4); GPT-5.4 primary for reasoning, GPT-4.1-mini for formatting and lightweight inference |
| Agents & orchestration | Microsoft Agent Framework | 7 agents including Master Orchestrator, 6 specialist agents, and Fallback Advisor |
| Structured Input / Output | Pydantic v2 | Strict typed schemas for all agent inputs and outputs; enforced structured output contracts between agents |
| MCP server | FastMCP | Exposes 3 tools (predict, diagnose, simulate) to agents over stdio transport |
| Web UI | Gradio | 5-tab conversational interface with 6 quick-action buttons and natural language chat input |
| ML models | Scikit-learn (sklearn) | Two-stage Random Forest: Stage 1 binary delay classifier (89.6% accuracy), Stage 2 severity classifier (Short/Medium/Long, 63.7% accuracy); evaluated against LinearRegression, DecisionTree, AdaBoost, XGBoost, LightGBM |
| Feature Engineering | Pandas, NumPy, SciPy | 10+ engineered features including interaction terms and ordinal encodings |
| Training data | Kaggle | 20,000 historical delivery records + 5,000 incremental daily / test records |
| Structured Database | SQLite3 | 27 tables storing current and historical delivery trends and patterns (12 summary types × daily + hist epochs, plus raw prediction tables and metadata dictionary) |
| Vector store / RAG | ChromaDB | Persistent vector database over SLA/SOP policy documents (1536-dim embeddings) |
| RAG text chunking | langchain-text-splitters | MarkdownHeaderTextSplitter + RecursiveCharacterTextSplitter pipeline that chunks the SLA document |
| LLM for Embedding | OpenAI text-embedding-3-small | Embedding model applied to SLA/SOP document chunks for semantic retrieval |
| RAG retrieval | Hybrid scoring | Stage 1: ChromaDB cosine top-15 → Stage 2: hybrid re-score (0.7 × cosine + 0.3 × keyword, top-12) |
| Cross-encoder Reranker | sentence-transformers (`cross-encoder/ms-marco-MiniLM-L-6-v2`) | Stage 3 RAG: cross-encoder reranking of top-12 hybrid results to top-8; improves SLA grounding precision |
| Configuration | python-dotenv, YAML | Environment and configuration management; `.env` for secrets, YAML for model hyperparameters |
| EDA & Visualisation | Matplotlib, Seaborn | Exploratory data analysis and charting during model training phase |
| Package Manager | uv / uv.lock + requirements.txt | Dependency management and reproducible installs; uv.lock pins exact versions, requirements.txt exported for compatibility |
| Testing | Pytest | Test files covering feature engineering, Pydantic models, and the RAG vector store |
| Python | 3.11+ | Runtime |
