# ML Prediction Pipeline — Architecture & Design

> This document describes the training pipeline implemented in  
> `prediction_pipeline/notebooks/train_predict_delay_model.ipynb`.

## Pipeline Flow

```perl
 ┌─────────────────────────────────────────────────────────┐
 │           DATA EXTRACTION & INSPECTION                  │
 │  Load Kaggle CSV (25,000 rows x 15 cols)                │
 │  Inspect shape, head, info, nulls                       │
 └──────────────────────┬──────────────────────────────────┘
                        │
                        ▼
 ┌─────────────────────────────────────────────────────────┐
 │           DATA CLEANING                                 │
 │  Standardise names, parse dates, compute slack_time     │
 │  Median/mode imputation, prune post-delivery leakage    │
 └──────────────────────┬──────────────────────────────────┘
                        │
                        ▼
 ┌─────────────────────────────────────────────────────────┐
 │           EXPLORATORY DATA ANALYSIS (EDA)               │
 │  Part 1: Pre-outlier stats & plots                      │
 │  Part 2: Smart outlier removal (selected numeric cols)  │
 │  Part 3: Post-outlier plots, correlations               │
 └──────────────────────┬──────────────────────────────────┘
                        │
                        ▼
 ┌─────────────────────────────────────────────────────────┐
 │           FEATURE ENGINEERING (Pre-Split)               │
 │  Interaction: weight_x_distance, km_per_expected_hr,    │
 │               cost_per_kg                               │
 │  Ordinal/Risk: weather_severity, mode_urgency,          │
 │                schedule_risk, vehicle_load_strain       │
 │  Group Agg: carrier_avg_schedule                        │
 │  Targets: delay_hours, delayed (binary)                 │
 └──────────────────────┬──────────────────────────────────┘
                        │
                        ▼
 ┌─────────────────────────────────────────────────────────┐
 │           FEATURE SELECTION                             │
 │  Drop correlated: delivery_cost, cost_per_km,           │
 │                  expected_time_hrs_num delivery_rating  │
 │  Drop post delivery cols:  delivery_rating              │
 └──────────────────────┬──────────────────────────────────┘
                        │
                        ▼
 ┌─────────────────────────────────────────────────────────┐
 │           TRAIN / TEST SPLIT                            │
 │  Stratified split on delayed target                     │
 │  Align delivery_id with train/test for traceability     │
 └──────────────────────┬──────────────────────────────────┘
                        │
               ┌────────┴────────┐
               ▼                 ▼
 ┌──────────────────┐  ┌──────────────────┐
 │ Post-Split 3a    │  │ Post-Split 3b    │
 │ One-Hot Encoding │  │ One-Hot + Scaling│
 │ (tree models)    │  │ (linear/SVM)     │
 └────────┬─────────┘  └────────┬─────────┘
          └────────┬────────────┘
                   ▼
 ┌─────────────────────────────────────────────────────────┐
 │           BASELINE MODELS                               │
 │  Logistic Regression (classification baseline)          │
 │  Linear Regression (regression baseline)                │
 └──────────────────────┬──────────────────────────────────┘
                        │
                        ▼
 ┌─────────────────────────────────────────────────────────┐
 │     STAGE 1: BINARY CLASSIFICATION — delayed (Yes/No)   │
 │                                                         │
 │  Train & evaluate 7 classifiers:                        │
 │    Logistic Reg, Decision Tree, Random Forest,          │
 │    AdaBoost, XGBoost, LightGBM, SVM, Naive Bayes        │
 │                                                         │
 │  Compare all → Best: Random Forest                      │
 │  Save best_classification_random_forest.pkl             │
 └──────────────────────┬──────────────────────────────────┘
                        │
                        ▼
 ┌─────────────────────────────────────────────────────────┐
 │     STAGE 2: TWO-STAGE PIPELINE (Classify → Severity)   │
 │                                                         │
 │  Filter to predicted-delayed rows only                  │
 │                                                         │
 │  ┌─────────────────────┐  ┌──────────────────────────┐  │
 │  │ Option 1 (chosen)   │  │ Option 2 (comparison)    │  │
 │  │ Severity Classif.   │  │ Ordinal Regression       │  │
 │  │ RF → Short/Med/Long │  │ Frank & Hall cumulative  │  │
 │  │ (1-2h / 3-5h / 6+h) │  │ threshold RFs → hours    │  │
 │  └─────────┬───────────┘  └──────────┬───────────────┘  │
 │            │   Best: Option 1        │                  │
 └────────────┼─────────────────────────┼──────────────────┘
              │                         │
              ▼                         │
 ┌─────────────────────────────────────────────────────────┐
 │           END-TO-END EVALUATION                         │
 │  Chain Stage 1 (binary) + Stage 2 (severity) on full    │
 │  test set; non-delayed → "No Delay" class               │
 │  Metrics: accuracy, F1, confusion matrices              │
 └──────────────────────┬──────────────────────────────────┘
                        │
                        ▼
 ┌─────────────────────────────────────────────────────────┐
 │           MODEL PERSISTENCE                             │
 │  Save best_severity_random_forest.pkl + metadata JSON   │
 │  Reload & verify both stage-1 and stage-2 models        │
 └──────────────────────┬──────────────────────────────────┘
                        │
                        ▼
 ┌─────────────────────────────────────────────────────────┐
 │           DATABASE & PREDICTIONS                        │
 │  Run two-stage predictions on train + test sets         │
 │  Save to delivery_predictions.db (SQLite)               │
 │  Build hist_* and daily_* summary tables                │
 │  Write prediction CSVs                                  │
 │  Verify: assert row counts, IDs, severity labels        │
 └─────────────────────────────────────────────────────────┘
 ```

**Pipeline summary:**

- Extract raw logistics data (25K orders)
- Clean and impute, removing post-delivery leakage columns
- EDA with outlier detection and removal
- Engineer 8 derived features (interaction, ordinal, group-aggregate)
- Select features by dropping correlated redundancies
- Split stratified train/test, then two encoding paths (tree vs linear)
- Stage 1 — train 8 binary classifiers on delayed, pick Random Forest
- Stage 2 — on predicted-delayed subset, train severity classifier (Short/Medium/Long); compare against ordinal regression alternative
- Persist both models (.pkl + metadata JSON), reload and verify
- Generate full predictions, save to SQLite DB with summary tables, write CSVs, and assert correctness

## Pipeline Summary

| Stage | Module | Responsibility |
|---|---|---|
| 1. Data Extraction | `data_extract_1.py` | Load CSV, validate column presence, inspect dtypes and nulls |
| 2. EDA | `data_eda_2.py` | Compute descriptive statistics, correlation matrix, class distribution |
| 3. Preprocessing | `data_processing_3.py` | Handle missing values, standardise categorical labels, remove duplicates |
| 4. Feature Engineering | `feature_engineering_4.py` | Create 8 derived features (see below) |
| 5. Evaluation Framework | `model_evaluation_5.py` | Define scoring functions: accuracy, precision, recall, F1, AUC |
| 6. Baseline Models | `baseline_models_6.py` | Logistic Regression and dummy classifiers for comparison |
| 7. Regression Models | `regression_models_7.py` | Ridge, Lasso, RF, XGB, LGBM for delay-hours regression |
| 8. Classification Models | `classification_models_8.py` | LR, RF, XGB, LGBM with GridSearchCV for binary classification |
| 9. Model Persistence | `model_persistence_9.py` | Pickle models + JSON metadata; reload with feature alignment |
| 10. Database Operations | `database_operations_10.py` | Write 27 SQLite tables: predictions + 12 daily + 12 historical summaries |
| 11. Test Data Generation | `generate_daily_test_data_11.py` | Synthetic daily batches (3 × 5,000 rows) |

Daily inference input is synthetic (stratified sampling from the training distribution with controlled delay-rate injection) rather than a live order feed — production live-order integration requires real-time infrastructure outside the scope of this demonstration, while synthetic data preserves realistic statistical properties and enables reproducible evaluation.

---

## Two-Stage Prediction Design

A key architectural decision is the sequential two-stage prediction approach, which separates the binary delay detection problem from the severity estimation problem. A single four-class model (on-time / short / medium / long) was considered and rejected for the following reasons:

- **Different class distributions per task.** The binary task has a 73%/27% on-time/delayed split — class-weight balancing tuned for recall maximization is the right approach here. The severity task (among delayed orders only) has a ~50% Short / 40% Medium / 12% Long distribution, requiring different calibration. A single four-class model would have the 73% on-time class overwhelm all three severity classes.
- **Independent hyperparameter tuning.** Splitting into two stages allows Stage 1 to optimise for recall (catching as many delays as possible) while Stage 2 optimises for overall accuracy across the three severity tiers — objectives that are in tension in a single model.
- **Cleaner evaluation.** A two-stage pipeline allows each stage to be evaluated independently against its own target metric before the end-to-end chain is assessed.

Stage 1 output feeds directly into Stage 2: only records predicted as delayed in Stage 1 are passed to Stage 2 for severity estimation. On-time orders receive `severity = None`.

### Stage 2 — Two Approaches Compared

For severity estimation, two methods were evaluated:

| Approach | Method | Result |
|---|---|---|
| **Option 1 — Severity Classification** (chosen) | Random Forest trained on three-class severity labels (Short / Medium / Long) using the same 8 engineered features | 63.7% accuracy · 65.6% weighted F1 |
| Option 2 — Ordinal Regression | Frank & Hall cumulative-threshold method with multiple RF classifiers; outputs a continuous delay-hours estimate bucketed into severity tiers | Lower performance on weighted F1; less interpretable thresholds |

**Option 1 (Severity Classification) was selected** because it directly models the three-class target that the downstream application needs, avoids the threshold-calibration overhead of ordinal regression, and produces the same feature importance structure as Stage 1 — making both models jointly interpretable.

---

## Feature Engineering — 8 Derived Features

Feature engineering is the most analytically significant step in the pipeline. 8 derived features are computed from the raw columns to capture higher-order relationships that individual raw fields cannot express.

### Interaction Features

| Feature | Formula | Business Interpretation |
|---|---|---|
| `weight_x_distance` | `package_weight_kg × distance_km` | Combined complexity proxy — heavier packages over longer routes strain capacity |
| `km_per_expected_hr` | `distance_km ÷ (expected_time_hours + ε)` | **Strongest predictor (27.1% importance, r ≈ 0.59 with delayed)** — tight schedules relative to distance drive delays |
| `cost_per_kg` | `delivery_cost ÷ (package_weight_kg + ε)` | Weight-adjusted pricing — heavier packages with low cost-per-kg may be deprioritised |

### Ordinal Risk Features

| Feature | Encoding | Values | Purpose |
|---|---|---|---|
| `weather_severity` | Ordinal (0–4) | 0=Clear, 1=Hot/Cold, 2=Foggy, 3=Rainy, 4=Stormy | Direct weather risk score |
| `mode_urgency` | Ordinal (1–4) | 1=Standard, 2=Two-Day, 3=Express, 4=Same-Day | Delivery mode risk level |
| `schedule_risk` | `weather_severity × mode_urgency` | 0–16 | Compounded risk — high score = tight deadline in bad weather |
| `vehicle_load_strain` | `(package_weight_kg × distance_km) ÷ vehicle_capacity` | Continuous | Load normalised by vehicle capability — overload indicator |

### Group Aggregate Features

One feature captures partner-level patterns by computing means over the training set, providing contextual historical behaviour signals:

| Feature | Aggregation | Values | Purpose |
|---|---|---|---|
| `carrier_avg_schedule` | Mean `km_per_expected_hr` per `delivery_partner` | Continuous float | Identifies partners who consistently accept routes too tight for their fleet |

### Encoding

After the above features are computed, one-hot encoding (`drop_first=True`) is applied to six categorical columns: `delivery_partner`, `package_type`, `vehicle_type`, `delivery_mode`, `region`, and `weather_condition`. This expands the feature matrix to approximately 85–120 columns depending on cardinality. Encoding is applied **after** the train/test split to prevent data leakage.

---

## Model Selection

### Stage 1 — Binary Delay Classification

Four candidate classifiers were evaluated using **StratifiedKFold cross-validation (3 folds)** with `scoring='recall'` as the primary metric. Recall was chosen over accuracy or F1 because false negatives (missed delay predictions) are significantly more costly operationally than false alarms — an operation that fails to anticipate a delay cannot take corrective action; one that predicts a false delay simply has a slightly unnecessary intervention.

Candidates evaluated:
- Logistic Regression (baseline)
- Random Forest ← **selected**
- XGBoost
- LightGBM

**Random Forest was selected** as the production model for both stages because it consistently outperformed the alternatives on recall across all three cross-validation folds, is robust to feature scale differences (no normalisation required), handles correlated features well, and produces interpretable feature importance outputs that directly inform operational action.

**GridSearchCV optimal hyperparameters:**

| Hyperparameter | Search Space | Optimal |
|---|---|---|
| `n_estimators` | 150, 200, 300 | 200 |
| `max_depth` | None, 10, 20 | None (fully grown) |
| `min_samples_split` | 2, 5 | 2 |
| `class_weight` | balanced, None | balanced |

**Stage 1 results:** 89.6% accuracy · 81.5% recall · 96.7% ROC-AUC

### Stage 1 — Feature Importances

The Random Forest provides Gini-impurity-based feature importances after training. The top features confirm that the engineered interaction and risk features dominate over raw inputs:

| Rank | Feature | Importance | Type | Operational Meaning |
|---|---|---|---|---|
| 1 | `km_per_expected_hr` | **27.1%** | Interaction | Schedule tightness relative to distance — the single strongest predictor; overly optimistic windows are the primary delay driver |
| 2 | `mode_urgency` | **21.5%** | Ordinal risk | Delivery mode urgency — Express and Same-Day compress the window and carry the highest SLA risk |
| 3 | `schedule_risk` | **14.9%** | Ordinal risk | Compounded `weather_severity × mode_urgency` — bad weather on a tight-deadline mode amplifies risk non-linearly |
| 4 | `vehicle_load_strain` | ~10% | Interaction | Weight × distance load relative to vehicle capacity — overloaded vehicles miss time windows |
| 5 | `carrier_avg_schedule` | ~8% | Group aggregate | Partner-level pattern — some partners systematically accept routes too tight for their fleet |

**Top 3 features together account for >63% of all model decisions.** All three are engineered features, not raw inputs — confirming that feature engineering was the most analytically significant step in the pipeline. None of the raw categorical columns (`delivery_partner`, `vehicle_type`, `region`) appear in the top 5.

**Key finding — feature engineering ROI vs. model selection ROI:** swapping between Random Forest, XGBoost, and LightGBM moved recall by only 2–3 percentage points. Engineering `km_per_expected_hr` and `schedule_risk` alone moved recall by ~15 points. In structured tabular ML problems like this one, domain-informed feature design has significantly higher ROI than model selection or hyperparameter tuning.

### Stage 2 — Severity Classification

The same Random Forest architecture was used for Stage 2, re-trained on the delayed-only subset with severity (Short / Medium / Long) as the target. GridSearchCV was re-run independently with the same hyperparameter grid; final parameters matched Stage 1 (200 estimators, fully grown trees, balanced class weights).

**Stage 2 results:** 63.7% accuracy · 65.6% weighted F1

The lower accuracy relative to Stage 1 reflects the harder nature of the severity problem: the three-class distribution (50.6% Short / 39.7% Medium / 11.9% Long) is imbalanced, and the feature signal that distinguishes severity tiers is weaker than the binary delayed/on-time signal. Weighted F1 is the appropriate primary metric here because it accounts for the class imbalance.

---

## Model Persistence & Metadata

Trained models are serialised to pickle files alongside JSON metadata files. The metadata captures feature names (in training order), feature importances, class labels, and training configuration. **This is critical for inference-time feature alignment**: the daily prediction pipeline (`daily_predict.py`) reorders and fills missing columns to exactly match the training feature set before calling `model.predict()`. Without this alignment step, batch data with different column ordering or missing one-hot columns would silently produce incorrect predictions.

| Artifact | Format | Contents |
|---|---|---|
| `best_classification_random_forest.pkl` | Pickle | Fitted `RandomForestClassifier` (Stage 1) |
| `best_classification_random_forest_metadata.json` | JSON | `feature_names_in_`, `n_features_in_`, `classes_`, `feature_importances_` |
| `best_severity_random_forest.pkl` | Pickle | Fitted `RandomForestClassifier` (Stage 2, 3-class severity) |
| `best_severity_random_forest_metadata.json` | JSON | Severity class definitions and feature config |
