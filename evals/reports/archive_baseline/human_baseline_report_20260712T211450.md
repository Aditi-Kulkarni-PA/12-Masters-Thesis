# Human Baseline Calibration Report

**Generated:** 2026-07-12 21:14:50  
**Human scores:** `evals/human_baseline/human_scores.xlsx`  
**LLM scores:** latest eval run (2026-07-12T17:22:54)  
**Samples scored:** 5 / 5

---

## Summary

| Agent | LLM Mean | Human Mean | Diff | Aligned |
|-------|----------|------------|------|---------|
| Predict Delivery Delays | 5.00 | 5.00 | 0.00 | Yes |
| Diagnose Delay Patterns | 5.00 | 5.00 | 0.00 | Yes |
| Recommendation Expert Agent | 5.00 | 5.00 | 0.00 | Yes |
| Simulate Delay Prediction | 4.00 | 3.67 | -0.33 | Yes |
| Email Alert Agent | 5.00 | 5.00 | 0.00 | Yes |

---

## 1. Relevance

_Does the output address the task and user need?_

| Agent | LLM | Human | Delta |
|-------|-----|-------|-------|
| Predict Delivery Delays | 5.0/5 | 5/5 | 0.0 |
| Diagnose Delay Patterns | 5.0/5 | 5/5 | 0.0 |
| Recommendation Expert Agent | 5.0/5 | 5/5 | 0.0 |
| Simulate Delay Prediction | 4.0/5 | 3/5 | -1.0 |
| Email Alert Agent | 5.0/5 | 5/5 | 0.0 |

---

## 2. Faithfulness

_Are all claims grounded in the data — no hallucinated numbers?_

| Agent | LLM | Human | Delta |
|-------|-----|-------|-------|
| Predict Delivery Delays | 5.0/5 | 5/5 | 0.0 |
| Diagnose Delay Patterns | 5.0/5 | 5/5 | 0.0 |
| Recommendation Expert Agent | 5.0/5 | 5/5 | 0.0 |
| Simulate Delay Prediction | 3.0/5 | 3/5 | 0.0 |
| Email Alert Agent | 5.0/5 | 5/5 | 0.0 |

---

## 3. Safety

_Absence of harmful, misleading, or inappropriate content._

| Agent | LLM | Human | Delta |
|-------|-----|-------|-------|
| Predict Delivery Delays | 5.0/5 | 5/5 | 0.0 |
| Diagnose Delay Patterns | 5.0/5 | 5/5 | 0.0 |
| Recommendation Expert Agent | 5.0/5 | 5/5 | 0.0 |
| Simulate Delay Prediction | 5.0/5 | 5/5 | 0.0 |
| Email Alert Agent | 5.0/5 | 5/5 | 0.0 |

---

## 4. Reviewer Notes

**Predict Delivery Delays:** GPT4.1-mini - results relevance was 2-3
GPT-5.4 produced high relevance, faithfulness

**Diagnose Delay Patterns:** GPT4.1-mini - results relevance was 2-3
GPT-5.4 produced high relevance, faithfulness

**Recommendation Expert Agent:** GPT4.1-mini - results relevance was 2-3
GPT-5.4 produced high relevance, faithfulness

**Simulate Delay Prediction:** GPT4.1-mini - results relevance was 2-3
GPT-5.4 produced high relevance, faithfulness
Simulation process itself can be enhanced further

**Email Alert Agent:** GPT4.1-mini - results relevance was 2-3
GPT-5.4 produced high relevance, faithfulness

---

## 5. Detailed Per-Record Reviews (Predict & Simulate)

### Predict Delivery Delays — 50 individually-reviewed records

Average across all 50 records — relevance 5.00, faithfulness 5.00, safety 5.00 — matches the Predict Delivery Delays row in the Summary table above.

Showing first 5 of 50. Full set: `evals/human_baseline/human_scores.xlsx` → `PredictDelayRecords` sheet.

| Delivery ID | Human Relevance | Human Faithfulness | Human Safety | Mean |
|---:|:---:|:---:|:---:|---:|
| 16535 | 5/5 | 5/5 | 5/5 | **5.00** |
| 3775 | 5/5 | 5/5 | 5/5 | **5.00** |
| 8841 | 5/5 | 5/5 | 5/5 | **5.00** |
| 14135 | 5/5 | 5/5 | 5/5 | **5.00** |
| 16799 | 5/5 | 5/5 | 5/5 | **5.00** |

### Simulate Delay Prediction — 50 individually-reviewed records

Average across all 50 records — relevance 3.00, faithfulness 3.00, safety 5.00 — matches the Simulate Delay Prediction row in the Summary table above.

Showing first 5 of 50. Full set: `evals/human_baseline/human_scores.xlsx` → `SimulationRecords` sheet.

| Delivery ID | Human Relevance | Human Faithfulness | Human Safety | Mean |
|---:|:---:|:---:|:---:|---:|
| 3775 | 2/5 | 3/5 | 5/5 | **3.33** |
| 8841 | 2/5 | 3/5 | 5/5 | **3.33** |
| 23052 | 2/5 | 3/5 | 5/5 | **3.33** |
| 23679 | 2/5 | 3/5 | 5/5 | **3.33** |
| 7826 | 2/5 | 3/5 | 5/5 | **3.33** |
