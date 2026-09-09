# Gradio UI Design — ChatInterface vs Manual Chatbot

## ChatInterface vs Chatbot

## Gradio UI Design Comparison

| Feature | gr.ChatInterface | Manual gr.Chatbot + gr.Textbox (current) |
|---|---|---|
| Layout | Single-column, chat only | Two-column: chat left, output tabs right |
| Streaming yields | Only `(text)` or `(history)` — one output | Can yield **15-tuple**: history + pending state + all 12 tab outputs simultaneously |
| Structured outputs | No way to populate separate tabs (Predict, Diagnosis, Simulation, etc.) from same handler | Each yield updates chatbot **AND all right-side tabs** in one pass |
| Action plan / confirmation | No state management — no way to stash a pending query | `gr.State("")` for `pending_state` enables **plan → confirm → execute** flow |
| Quick-action buttons | Can't have buttons that auto-submit preset text | Buttons `.click()` → populate textbox → `.then()` calls `chat_handler` |
| File upload wiring | Limited — `additional_inputs` exist but awkward to wire | `orders_file` is a normal input wired into the handler directly |
| Clear behavior | Built-in clear only resets chat | Custom clear resets chat + textbox + pending state + shows welcome message |
| Initial welcome message | `chatbot` parameter exists but limited control | `value=[{"role": "assistant", "content": _WELCOME_MSG}]` — full control |

**Key reason for the manual approach**

`gr.ChatInterface` controls its own layout and only yields chat messages. There is no way to have a single `chat_handler` that streams updates to both the chatbot **and 12+ output components** (markdown, dataframes, file downloads) across **5 tabs**.

The manual `gr.Chatbot + gr.Textbox` setup allows a **15-element yield tuple** that updates **chat + state + all structured outputs simultaneously**.

---

## Application Layout

The delivery app (`delivery_chat_app.py`, 700+ lines) presents a two-panel Gradio interface: a chat panel on the left for natural language interaction and a tabbed results panel on the right for structured outputs.

### Chat Panel Elements

| Element | Purpose |
|---|---|
| Chat history window | Scrollable conversation log; preserves full session history |
| File upload button | Accepts CSV files; triggers ingestion into the prediction pipeline |
| Text input box | Natural language query entry |
| Send button | Submits query to the agent orchestrator |
| 6 quick-action buttons | Pre-composed queries for Predict, Diagnose, Simulate, Recommend, Email, Full Analysis |
| Clear chat button | Resets conversation history, output tabs, pending state, re-shows welcome message |

### Results Tabs (5 Tabs)

| Tab | Contents | Data Source |
|---|---|---|
| Predict | Markdown prediction summary + interactive DataFrame + CSV download | Predict Agent output + prediction CSV |
| Diagnosis | High-risk patterns DataFrame + daily vs historical comparison DataFrame | Diagnosis Agent output + SQLite summary tables |
| Simulation | Markdown scenario narrative + simulation results DataFrame + CSV download | Simulation Agent output |
| Recommendation | Markdown action items + structured recommendations DataFrame | Recommendation Agent + RAG-retrieved SLA context |
| Email | Markdown email templates per affected customer + bulk CSV export | Email Agent output |

---

## Intent Detection

The app includes a lightweight intent detection layer (`app_utils.py`) that maps user messages to tool chains using regex pattern matching, allowing the system to respond appropriately to a wide range of phrasings without requiring exact keyword matching.

**Simple intents:** A query containing phrases like "which orders", "predict", "delay today" maps to the `PREDICT` intent.

**Composite intents:** A single query can imply multiple tools — these are handled through intent combination logic:

| Query pattern | Mapped intent chain |
|---|---|
| "SLA performance", "full analysis" | `[predict, diagnose, recommend]` — three-stage pipeline |
| "root cause" / "why are orders delayed" | `[predict, diagnose]` |
| "what should we do" / "recommendations" | `[predict, diagnose, recommend]` |
| "simulate" / "what if" | `[predict, simulate]` |
| "email" / "notify customers" | `[predict, email]` |

Composite intents trigger automatic prerequisite chaining — the Master Orchestrator enforces the data dependency order even when the user only named the final output.

---

## Format Agent

The Format Summary agent has been replaced by deterministic Python formatting in `helpers/post_processing.py` — every tab's display is now built in code from validated sub-agent row data. The agent remains defined in `delivery_agents.py` and available for future use/changes.

**Full Format Agent design, output structure per summary type, and formatting rules:** [`docs/19-format-agent-design.md`](19-format-agent-design.md)
