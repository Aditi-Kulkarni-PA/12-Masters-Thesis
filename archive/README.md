# archive/

Kept for reference, not for running.

## `supply_chain_delivery_app_openai_sdk/`

The original OpenAI Agents SDK implementation — the frozen `"baseline"` stack in
`evals/env_settings.py`. It is the reference the MAF replica was checked against in T33
(PASS: judge deltas all 0.00; RAGAS no regression).

**It does not need to run again.** `evals/run_parity_check.sh` reads the frozen
`judge_scores_openai_sdk_baseline.json` in `evals/reports/parity_reference_maf_pre_refactor/`
— the parity evidence lives in that JSON, not in this source tree. The code is retained
only so the comparison can be inspected if an examiner asks what the baseline actually was.

Do not edit it. A change here would silently invalidate the T33 parity claim, since the
frozen scores could no longer be reproduced from this source.
