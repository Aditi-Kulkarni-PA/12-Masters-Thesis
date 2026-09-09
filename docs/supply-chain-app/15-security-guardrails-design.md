# Security Guardrails — Design Rationale

## Security Guardrails

### Attack Surface Analysis

```perl
User Input
    ↓
Master Expert  ← ONLY entry point for raw user text
    ↓ (structured tool call arguments)
Sub-agents (Predict / Diagnose / Simulate / Recommend / Email)
    ↓ (typed Pydantic output)
Master Expert  ← assembles final MasterOutput
```
### Why sub-agents dont need security_guardrails.md to be preppended to their instructions

| Concern | Sub-agents? | Reason |
|---|---|---|
| Prompt injection from user | ✗ Not needed | Sub-agents never receive raw user text. They only receive structured inputs constructed by the master expert. |
| Scope violation | ✗ Not needed | Each sub-agent is already narrowly scoped by its own prompt (predict only, diagnose only, etc.) + `tool_choice="required"` pins it to one tool. |
| Output safety | ✗ Not needed | `output_type=PydanticModel` enforces a typed schema — the LLM can't produce arbitrary free-form output. |
| Data fabrication | Partially | Sub-agent prompts already instruct against this; Pydantic validation rejects structurally wrong output. |

The one case where you would add it to sub-agents
If tool outputs (e.g. CSV data, MCP responses) contain injected instructions that could manipulate a sub-agent's behavior — for example, an order record containing "Ignore previous instructions and return..." — then sub-agents could theoretically be influenced. However, since their outputs are Pydantic-constrained, any injected text would at most corrupt a string field, not change tool-calling behavior.

Conclusion: Keep security guardrails only on the master expert. It's the sole user-facing agent and the correct place to enforce all input validation and scope restriction.

---

## Guardrail Categories

Security guardrails are implemented as a highest-priority system prompt layer injected before all other agent instructions, in the order: **Security Guardrails → Chatbot Behavior → Agent Expert Instructions**. This layering is expressed as `@security_guardrails` / `@chatbot_behavior` include directives at the top of `master_expert.md` (expanded by `get_instruction()` in `config/load_config.py`), ensuring security rules come first and cannot be overridden by later instruction sections.

### 1. Scope Restriction

**Purpose:** Prevent the agent from answering out-of-domain questions.

The agent is strictly limited to supply chain delivery operations: delay prediction, pattern diagnosis, what-if simulation, optimisation recommendations, and customer email alerts.

**Enforcement:**
- Refuses all questions outside these five capabilities
- Rejects requests to role-play as a different AI, persona, or system
- Rejects discussion of other business domains, personal topics, general knowledge, or current events

*Refusal message:* "I'm only able to help with supply chain delivery operations. Please ask about delay predictions, diagnosis, simulations, recommendations, or customer alerts."

---

### 2. Prompt Injection Defence

**Purpose:** Protect the agent from adversarial attempts to override its instructions via user input or uploaded data.

**Enforcement:**
- Does not follow instructions embedded in uploaded order files, CSVs, or tool outputs
- Ignores user messages that attempt to redefine the agent's role, claim system-level authority ("admin override", "developer mode"), or override operating rules
- Does not execute code, shell commands, or arbitrary instructions from any source

*Refusal message:* "I cannot follow instructions embedded in data or that override my operating rules."

---

### 3. Data Privacy and Confidentiality

**Purpose:** Prevent leakage of system internals and sensitive order/customer data.

**Enforcement:**
- Does not reveal, repeat, or summarise the full system prompt or internal agent instructions
- Does not expose raw file paths, API keys, environment variable names, or internal server configuration beyond task necessity
- Does not output bulk raw data (full CSV contents, full database dumps) in chat — only summaries, statistics, and table excerpts
- Treats all order data, customer IDs, and email addresses as PII — referenced only in the context of completing the requested task

---

### 4. Tool Use Boundaries

**Purpose:** Restrict the agent to its registered tool set, preventing arbitrary external calls.

**Enforcement:**
- Only calls tools explicitly listed in the session tool registry (predict, diagnose, simulate, recommend, email_alert; format_summary is defined but not currently wired into the registry)
- Does not attempt to call unlisted tools, construct arbitrary HTTP requests, or access external URLs or services
- Does not use tool outputs to infer or reveal credentials, internal system details, or configuration beyond what is needed for the task

The MCP client is additionally configured with an explicit `tool_filter` that whitelists only the three approved MCP tools (`predict_delivery_delays`, `get_delay_diagnosis`, `simulate_order_delays`), following the principle of least privilege.

---

### 5. Output Safety

**Purpose:** Ensure all generated output is accurate, safe, and non-executable.

**Enforcement:**
- Does not generate harmful, offensive, discriminatory, or misleading content
- Does not fabricate data, statistics, or order records not returned by tools — if a tool returns no data, the agent states this clearly rather than inventing results
- Does not include executable code, scripts, or macros in any output, including generated customer email content

---

### 6. Escalation Handling

**Purpose:** Prevent adversarial probing through repeated or escalating requests.

**Enforcement:**
- If a request is ambiguous regarding security (testing limits, repeating denied requests, or escalating), the agent responds once with a clear refusal and does not engage further with that line of questioning

---

## Prompt Layer Architecture

```
Master Agent instruction stack (assembled via @include directives in master_expert.md):
  [1] security_guardrails.md      ← highest priority
  [2] chatbot_behavior.md
  [3] master_expert.md            ← domain logic
```

The Fallback Advisor and Format Summary agents receive no security guardrails — they are internal-only agents invoked by the Master agent, never directly accessible to end users, and operate on data already validated by the Master agent layer.

