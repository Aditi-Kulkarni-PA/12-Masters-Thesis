**Reflect-and-retry**

You have two distinct opportunities to react to a result. Keep them separate — they
are not the same mechanism.

1. **Structural recovery (mechanical, not a judgement call).** If a result comes back
   as `{"Error": "upstream_missing", ...}`, obtain the prerequisite it names, then
   retry the original step once. This is covered by `dependency_discovery.md` and does
   not count against the reflect-retry cap below.

2. **Reflect-retry (a judgement call — bounded).** A step can also return
   successfully while still not serving the user's request well — for example a
   simulation that matched zero rows because a scenario word was mapped to the wrong
   valid value, or a recommendation that looks generic given what you actually know.
   If you judge a *successful* result to be unsatisfactory:
   - You may retry that same step **once**, with a genuinely adjusted approach
     (different filter values, a clarified request, different inputs). Do not retry
     with identical arguments — that is not reflection, it is repetition.
   - If the retry is still unsatisfactory, stop. Report the limitation honestly in
     the relevant output field (§`output_contract.md`) rather than retrying again or
     presenting the weak result as if it were fully satisfactory.
   - A retry must still respect every constraint the underlying step defines — the
     accepted `weather_condition`, `region`, `vehicle_type` and `delivery_mode` values
     are fixed, and reflection does not grant access to any option a first attempt did
     not already have.

**Step budget: 10 per turn.** Count every step you initiate, whatever form your
interface gives it. The figure comes from the shape of the task — five domain
capabilities, each attempted at most twice (one first attempt plus one reflect-retry).
If you reach the limit, stop and report in your output what was and was not completed
and why. Do not silently truncate the user's request.

**Before returning your structured output, check:**
- Did you address every part of the user's request?
- For a full-workflow request, did you obtain (or attempt, within the budget above)
  every capability it requires?
- Are you about to restate something already captured directly from the results? If
  so, remove it — see `output_contract.md`.
