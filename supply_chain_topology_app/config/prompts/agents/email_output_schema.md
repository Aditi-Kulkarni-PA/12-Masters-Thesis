**Customer-email result — required output structure**

- `content` (list) — *List of emails to be sent to customers whose orders are delayed. One item per delayed order's sample email when the tool succeeds. Return an EMPTY list if the tool errored or found no delayed orders -- do NOT fabricate an explanatory email.*
  - `email_content` (str) — *Professional email body to notify the customer about their delayed order.*
  - `email_id` (str) — *Email ID of the customer; use a realistic placeholder if unknown.*
