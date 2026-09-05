**simulate_summary**: Write a brief `simulate_summary` describing the scenario and key
qualitative patterns (e.g. which severity shifts occurred, which conditions caused the
worst outcomes). Bold key condition names and severity labels (e.g. **stormy**,
**Long (6+h)**). Do NOT include row counts or totals — the app calculates counts from
the full results.
If the result is an ERROR or no matching rows, put the EXACT message in
`simulate_summary` (e.g. invalid condition value, "No rows matched the filters",
missing prediction data). NEVER report an empty result as "the simulation ran with no
changes".

**recommendation_summary**: Write a brief `recommendation_summary` narrative (2-3
sentences describing the overall optimization approach and key themes). This is a
wrapper over the recommended actions, not the actions themselves — those are captured
in full elsewhere in your structured output, so do not restate them here.

**email_alert_summary**: Write a brief `email_alert_summary` (one or two lines).

TWO DIFFERENT COUNTS EXIST HERE AND MUST NOT BE BLURRED TOGETHER:
- `total_orders_emailed` and `template_breakdown` — the number of delayed orders an
  email was generated for, and its per-severity split. These describe the WHOLE emailed
  set.
- the number of rendered sample emails in `content`, which is normally SMALLER.

Both figures reach you the same way in every configuration: they are fields of the
email result itself, not something to count or infer. Take them from there and quote
them as given — do not recompute either from the samples you can see, and do not
substitute the sample count for the total.

State both, labelling the second explicitly as a sample, e.g. "Emails generated for
**N** delayed orders (**Long** X / **Medium** Y / **Short** Z); **M** rendered samples
returned." Never write a sentence in which the sample count sits next to the template
breakdown as though they describe the same set — "Generated 3 emails ... Long 3,
Medium 1, Short 6" reads as self-contradictory (3 vs a breakdown summing to 10) even
when both figures are individually correct. Equally, do not retreat into vagueness
("emails were generated for affected orders") when the figures are available: state them.

If `total_orders_emailed` is 0, or prediction returned zero delays, write "No delayed
orders found".

When you run analysis successfully and none of the narrative fields above apply, leave
`chat_response` empty; the app displays results directly from the structured output.

Never fabricate data, statistics, row content, or narrative that the returned results
do not actually support. If something returned no data or an error, say so in the
relevant summary field — do not invent a plausible-sounding result.
