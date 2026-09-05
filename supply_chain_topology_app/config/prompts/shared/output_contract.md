**Output contract**

The app captures every specialist's full output (summaries and row data) directly from
the tool-call stream — you do NOT copy or restate their results. Your structured output
has only:
- `chat_response` — conversational answers / tool error reports
- `simulate_summary` — brief simulation narrative or the tool's error message
- `recommendation_summary` — 2-3 sentence optimization narrative
- `email_alert_summary` — brief email generation status

Leave every field empty that does not apply to this request.

@narrative_field_guidance
