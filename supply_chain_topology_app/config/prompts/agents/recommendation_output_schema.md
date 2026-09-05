**Recommendation result — required output structure**

- `recommended_actions` (list) — *List of recommended actions for delivery optimization. MUST contain at least 3 quick-win, 3 short-term, AND 3 long-term actions (9+ total).Return an EMPTY list if the underlying tool call failed.*
  - `action` (str) — *Recommendation Action - Short Description*
  - `action_desc` (str) — *Recommendation Action - Full Description with supporting data*
  - `category` (str, one of 'quick-win', 'short-term', 'long-term') — *One of: quick-win, short-term, long-term*
  - `dimension` (str) — *Which dimension this targets: delivery_mode, weather, region, vehicle, partner, or general*
  - `supporting_data` (str) — *Specific numbers from the analysis that justify this recommendation*
  - `sla_reference` (str) — *Quote the actual SLA text from the Retrieved Sections — include the section heading and specific metric, target, penalty, or rule. Example: 'SLA 2.1 On-Time Delivery Commitments by Mode: Express current target OTD is 40%. SLA 3.2 Weather-Specific Operational Protocols: Stormy — halt same-day and express dispatches if wind speed > 60 km/h.' Do NOT write generic labels like 'SLA Reference 3' — always quote the content itself.*
