"""
Database Operations Module

Saves prediction datasets and summary tables to SQLite for the two-stage
delay prediction pipeline (Stage 1: delayed Y/N, Stage 2: severity classification).

Tables created:
  - hist_delivery_delay_prediction   (training data + actuals + predictions)
  - daily_delivery_delay_prediction  (test/daily data + predictions only)
  - hist_summary_*  /  daily_summary_*  (pattern-analysis aggregates)
  - metadata_dictionary              (column descriptions for all tables)
"""

import sqlite3
import os
import re
import threading
import numpy as np
import pandas as pd
from typing import Optional, Dict, List, Literal

from pydantic import BaseModel, Field, validate_call

# Serializes refresh_daily()'s end-to-end table rebuild across threads within this
# process. save_daily_predictions/create_summary_tables/create_metadata_dictionary each
# replace tables via DROP+CREATE or to_sql(if_exists="replace") -- none atomic -- so two
# concurrent refreshes (e.g. two specialists both dispatching predict_delivery_delays at
# once) can interleave their drops and creates and raise "table already exists".
# anyio.to_thread.run_sync in prediction_server.py runs every MCP tool handler in this
# same process's thread pool, so a lock is sufficient here -- no cross-process
# contention to handle. Reentrant because refresh_daily() wraps its own call into
# create_summary_tables(), which also takes this lock when called on its own (e.g. the
# one-off hist-table build at startup).
_rebuild_lock = threading.RLock()


# Severity label maps (must match notebook definitions)
FULL_SEVERITY_LABELS: Dict[int, str] = {
    0: "No Delay",
    1: "Short (1-2h)",
    2: "Medium (3-5h)",
    3: "Long (6+h)",
}


class DailyPredictionConfig(BaseModel):
    """Validated configuration for daily prediction save / refresh operations.

    Agents and scripts can construct this model to get immediate input
    validation before calling save_daily_predictions() or refresh_daily().
    """

    db_path: str = Field(
        default="db/delivery_predictions.db",
        description="Path to the SQLite database file.",
    )
    table_name: str = Field(
        default="daily_delivery_delay_prediction",
        description="Target table name in the database.",
    )
    if_exists: Literal["replace", "append"] = Field(
        default="replace",
        description="How to handle existing table: 'replace' or 'append'.",
    )
    csv_dir: Optional[str] = Field(
        default=None,
        description="Directory for CSV export. None = skip CSV.",
    )
    severity_labels: Optional[Dict[int, str]] = Field(
        default=None,
        description="Severity label map. None = use FULL_SEVERITY_LABELS.",
    )


class DatabaseOperations:
    """Two-stage pipeline database operations: prediction tables, summary tables, metadata."""

    DEFAULT_DB_PATH = "db/delivery_predictions.db"

    # Categorical columns used for summary GROUP-BYs
    _CAT_COLS = [
        "delivery_partner",
        "package_type",
        "vehicle_type",
        "delivery_mode",
        "region",
        "weather_condition",
    ]

    # ------------------------------------------------------------------ #
    #  1. PREDICTION TABLES                                               #
    # ------------------------------------------------------------------ #

    # ---- internal helper: build a prediction DataFrame ----
    @staticmethod
    def _build_prediction_df(
        X: pd.DataFrame,
        ids: pd.Series,
        pred_delay: np.ndarray,
        pred_severity: np.ndarray,
        severity_labels: Dict[int, str],
        y_class: Optional[pd.Series] = None,
        y_reg: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """Build a single prediction DataFrame with optional actuals."""
        n = len(X)
        if len(ids) != n:
            raise ValueError(f"ids length ({len(ids)}) != X rows ({n})")
        if len(np.asarray(pred_delay).ravel()) != n:
            raise ValueError(f"pred_delay length ({len(np.asarray(pred_delay).ravel())}) != X rows ({n})")
        if len(np.asarray(pred_severity).ravel()) != n:
            raise ValueError(f"pred_severity length ({len(np.asarray(pred_severity).ravel())}) != X rows ({n})")

        df = X.copy()
        df.insert(0, "delivery_id", ids.values)
        if y_class is not None:
            df["actual_delayed"] = y_class.values
        if y_reg is not None:
            df["actual_delay_hours"] = y_reg.values
        df["predict_delay"] = np.asarray(pred_delay).ravel()
        df["predict_severity"] = np.asarray(pred_severity).ravel()
        df["predict_severity_label"] = df["predict_severity"].map(severity_labels)
        return df

    @classmethod
    def save_prediction_tables(
        cls,
        X_train: pd.DataFrame,
        X_test: pd.DataFrame,
        train_ids: pd.Series,
        test_ids: pd.Series,
        y_train_class: pd.Series,
        y_train_reg: pd.Series,
        train_pred_delay: np.ndarray,
        train_pred_severity: np.ndarray,
        test_pred_delay: np.ndarray,
        test_pred_severity: np.ndarray,
        severity_labels: Optional[Dict[int, str]] = None,
        db_path: str = "db/delivery_predictions.db",
        hist_table: str = "hist_delivery_delay_prediction",
        daily_table: str = "daily_delivery_delay_prediction",
        csv_dir: Optional[str] = "./data/processed",
        overwrite: bool = True,
    ) -> Dict[str, str]:
        """
        Build and save hist (train) and daily (test) prediction tables.

        hist table includes actuals (actual_delayed, actual_delay_hours)
        plus predictions.  daily table includes predictions only.

        Returns dict of {label: table_name}.
        """
        if severity_labels is None:
            severity_labels = FULL_SEVERITY_LABELS

        if overwrite and os.path.exists(db_path):
            os.remove(db_path)
            print(f"✓ Removed existing database: {db_path}")

        conn = sqlite3.connect(db_path)
        saved = {}

        try:
            # --- HIST (training) table ---
            hist = cls._build_prediction_df(
                X_train, train_ids, train_pred_delay, train_pred_severity,
                severity_labels, y_class=y_train_class, y_reg=y_train_reg,
            )
            hist.to_sql(hist_table, conn, if_exists="replace", index=False)
            saved["hist"] = hist_table
            print(f"✓ Saved {len(hist)} rows → {hist_table}")

            # --- DAILY (test) table ---
            daily = cls._build_prediction_df(
                X_test, test_ids, test_pred_delay, test_pred_severity,
                severity_labels,
            )
            daily.to_sql(daily_table, conn, if_exists="replace", index=False)
            saved["daily"] = daily_table
            print(f"✓ Saved {len(daily)} rows → {daily_table}")

            # --- Also save CSVs ---
            if csv_dir:
                os.makedirs(csv_dir, exist_ok=True)
                for label, df, tname in [("hist", hist, hist_table), ("daily", daily, daily_table)]:
                    csv_path = os.path.join(csv_dir, f"{tname}.csv")
                    df.to_csv(csv_path, index=False)
                    saved[f"{label}_csv"] = csv_path
                    print(f"✓ CSV → {csv_path}")

        finally:
            conn.close()

        print(f"\n✓ Prediction tables saved to {db_path}")
        return saved

    # ------------------------------------------------------------------ #
    #  1b. DAILY-ONLY PREDICTION TABLE (for inference / agent use)        #
    # ------------------------------------------------------------------ #

    @classmethod
    def save_daily_predictions(
        cls,
        X_daily: pd.DataFrame,
        daily_ids: pd.Series,
        pred_delay: np.ndarray,
        pred_severity: np.ndarray,
        severity_labels: Optional[Dict[int, str]] = None,
        db_path: str = "db/delivery_predictions.db",
        table_name: str = "daily_delivery_delay_prediction",
        if_exists: Literal["replace", "append"] = "replace",
        csv_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Save (or append) a daily prediction table without touching hist tables.

        Parameters
        ----------
        X_daily      : Feature DataFrame (same columns as training X).
        daily_ids    : Series of delivery_id values.
        pred_delay   : Binary delay predictions (0/1).
        pred_severity: Severity predictions (0-3).
        severity_labels : Label map (default: FULL_SEVERITY_LABELS).
        db_path      : Path to the SQLite database.
        table_name   : Name of the daily table.
        if_exists    : 'replace' (default) or 'append'.
        csv_dir      : Directory for CSV export (None = skip CSV).

        Returns dict of {label: path_or_table_name}.
        """
        if severity_labels is None:
            severity_labels = FULL_SEVERITY_LABELS

        daily = cls._build_prediction_df(
            X_daily, daily_ids, pred_delay, pred_severity, severity_labels,
        )

        conn = sqlite3.connect(db_path)
        saved = {}
        try:
            daily.to_sql(table_name, conn, if_exists=if_exists, index=False)
            saved["daily"] = table_name
            print(f"✓ Saved {len(daily)} rows → {table_name} (mode={if_exists})")

            if csv_dir:
                os.makedirs(csv_dir, exist_ok=True)
                csv_path = os.path.join(csv_dir, f"{table_name}.csv")
                if if_exists == "append" and os.path.exists(csv_path):
                    daily.to_csv(csv_path, mode="a", header=False, index=False)
                else:
                    daily.to_csv(csv_path, index=False)
                saved["daily_csv"] = csv_path
                print(f"✓ CSV → {csv_path}")
        finally:
            conn.close()

        return saved

    @classmethod
    def refresh_daily(
        cls,
        X_daily: pd.DataFrame,
        daily_ids: pd.Series,
        pred_delay: np.ndarray,
        pred_severity: np.ndarray,
        severity_labels: Optional[Dict[int, str]] = None,
        db_path: str = "db/delivery_predictions.db",
        table_name: str = "daily_delivery_delay_prediction",
        if_exists: Literal["replace", "append"] = "replace",
        csv_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        End-to-end daily refresh: save daily predictions → rebuild daily
        summary tables → update metadata dictionary.

        Returns dict with keys: daily, daily_csv (optional),
        summary_tables, metadata_count.
        """
        # Locked end-to-end: save + summary rebuild + metadata update must complete as
        # one unit, or a concurrent second refresh can read/overwrite a half-updated
        # database (see _rebuild_lock above).
        with _rebuild_lock:
            result = cls.save_daily_predictions(
                X_daily, daily_ids, pred_delay, pred_severity,
                severity_labels=severity_labels,
                db_path=db_path,
                table_name=table_name,
                if_exists=if_exists,
                csv_dir=csv_dir,
            )

            # Rebuild daily summary tables
            summary_tables = cls.create_summary_tables(
                db_path=db_path,
                source_table=table_name,
                prefix="daily_",
            )
            result["summary_tables"] = summary_tables

            # Update metadata dictionary for all tables
            meta_df = cls.create_metadata_dictionary(db_path=db_path)
            result["metadata_count"] = len(meta_df)

        print(f"\n✓ Daily refresh complete: {len(summary_tables)} summary tables, "
              f"{len(meta_df)} metadata entries")
        return result

    # ------------------------------------------------------------------ #
    #  2. SUMMARY TABLES (hist_ and daily_ prefixed)                      #
    # ------------------------------------------------------------------ #

    # Column contract used inside summary SQL.
    # hist tables have: actual_delayed, actual_delay_hours (actuals)
    # daily tables have: predict_delay, predict_severity (predictions only)
    # The SQL templates use {delayed_col} and {delay_hours_col} so the
    # same template works for both.

    @staticmethod
    def _summary_single_dim_sql(
        table_name: str,
        source: str,
        group_col: str,
        delayed_col: str,
        severity_col: str,
    ) -> str:
        """SQL to create + populate a single-dimension summary table."""
        return f"""
        DROP TABLE IF EXISTS {table_name};
        CREATE TABLE {table_name} AS
        SELECT
            {group_col},
            COUNT(*) AS total_deliveries,
            SUM(CASE WHEN {delayed_col} = 1 THEN 1 ELSE 0 END) AS delayed_count,
            SUM(CASE WHEN {delayed_col} = 0 THEN 1 ELSE 0 END) AS on_time_count,
            ROUND(CAST(SUM(CASE WHEN {delayed_col} = 1 THEN 1 ELSE 0 END) AS REAL) / COUNT(*), 4) AS delay_rate,
            SUM(CASE WHEN {severity_col} = 1 THEN 1 ELSE 0 END) AS severity_short_count,
            SUM(CASE WHEN {severity_col} = 2 THEN 1 ELSE 0 END) AS severity_medium_count,
            SUM(CASE WHEN {severity_col} = 3 THEN 1 ELSE 0 END) AS severity_long_count,
            ROUND(AVG(distance_km), 2) AS avg_distance_km,
            ROUND(AVG(package_weight_kg), 2) AS avg_package_weight_kg,
            ROUND(AVG(schedule_risk), 4) AS avg_schedule_risk
        FROM {source}
        WHERE {group_col} IS NOT NULL
        GROUP BY {group_col};
        """

    @staticmethod
    def _summary_distance_cat_sql(
        table_name: str,
        source: str,
        delayed_col: str,
        severity_col: str,
    ) -> str:
        """Summary by distance category (short < 50 km, medium 50-200, long > 200)."""
        return f"""
        DROP TABLE IF EXISTS {table_name};
        CREATE TABLE {table_name} AS
        SELECT
            CASE
                WHEN distance_km < 50  THEN 'short'
                WHEN distance_km < 200 THEN 'medium'
                ELSE 'long'
            END AS distance_category,
            CASE
                WHEN distance_km < 50  THEN '< 50 km'
                WHEN distance_km < 200 THEN '50-200 km'
                ELSE '> 200 km'
            END AS distance_range,
            COUNT(*) AS total_deliveries,
            SUM(CASE WHEN {delayed_col} = 1 THEN 1 ELSE 0 END) AS delayed_count,
            SUM(CASE WHEN {delayed_col} = 0 THEN 1 ELSE 0 END) AS on_time_count,
            ROUND(CAST(SUM(CASE WHEN {delayed_col} = 1 THEN 1 ELSE 0 END) AS REAL) / COUNT(*), 4) AS delay_rate,
            SUM(CASE WHEN {severity_col} = 1 THEN 1 ELSE 0 END) AS severity_short_count,
            SUM(CASE WHEN {severity_col} = 2 THEN 1 ELSE 0 END) AS severity_medium_count,
            SUM(CASE WHEN {severity_col} = 3 THEN 1 ELSE 0 END) AS severity_long_count,
            ROUND(AVG(distance_km), 2) AS avg_distance_km,
            ROUND(AVG(package_weight_kg), 2) AS avg_package_weight_kg,
            ROUND(AVG(schedule_risk), 4) AS avg_schedule_risk
        FROM {source}
        WHERE distance_km IS NOT NULL
        GROUP BY distance_category, distance_range;
        """

    @staticmethod
    def _summary_combined_sql(
        table_name: str,
        source: str,
        col_a: str,
        col_b: str,
        delayed_col: str,
        severity_col: str,
    ) -> str:
        """Two-dimension combined summary."""
        # If col_b is distance_category, do inline CASE
        if col_b == "distance_category":
            col_b_expr = (
                "CASE WHEN distance_km < 50 THEN 'short' "
                "WHEN distance_km < 200 THEN 'medium' ELSE 'long' END"
            )
            col_b_alias = "distance_category"
        else:
            col_b_expr = col_b
            col_b_alias = col_b

        return f"""
        DROP TABLE IF EXISTS {table_name};
        CREATE TABLE {table_name} AS
        SELECT
            {col_a},
            {col_b_expr} AS {col_b_alias},
            COUNT(*) AS total_deliveries,
            SUM(CASE WHEN {delayed_col} = 1 THEN 1 ELSE 0 END) AS delayed_count,
            SUM(CASE WHEN {delayed_col} = 0 THEN 1 ELSE 0 END) AS on_time_count,
            ROUND(CAST(SUM(CASE WHEN {delayed_col} = 1 THEN 1 ELSE 0 END) AS REAL) / COUNT(*), 4) AS delay_rate,
            SUM(CASE WHEN {severity_col} = 1 THEN 1 ELSE 0 END) AS severity_short_count,
            SUM(CASE WHEN {severity_col} = 2 THEN 1 ELSE 0 END) AS severity_medium_count,
            SUM(CASE WHEN {severity_col} = 3 THEN 1 ELSE 0 END) AS severity_long_count,
            ROUND(AVG(distance_km), 2) AS avg_distance_km,
            ROUND(AVG(schedule_risk), 4) AS avg_schedule_risk
        FROM {source}
        WHERE {col_a} IS NOT NULL
        GROUP BY {col_a}, {col_b_alias};
        """

    @staticmethod
    def _summary_overall_sql(
        table_name: str,
        source: str,
        delayed_col: str,
        severity_col: str,
    ) -> str:
        return f"""
        DROP TABLE IF EXISTS {table_name};
        CREATE TABLE {table_name} (
            stat_id   TEXT PRIMARY KEY,
            stat_name TEXT,
            stat_value REAL,
            description TEXT
        );
        INSERT INTO {table_name} (stat_id, stat_name, stat_value, description) VALUES
            ('total_deliveries', 'Total Deliveries',
             (SELECT COUNT(*) FROM {source}),
             'Total number of deliveries'),
            ('delayed_count', 'Total Delayed',
             (SELECT SUM(CASE WHEN {delayed_col} = 1 THEN 1 ELSE 0 END) FROM {source}),
             'Total delayed deliveries'),
            ('on_time_count', 'Total On-Time',
             (SELECT SUM(CASE WHEN {delayed_col} = 0 THEN 1 ELSE 0 END) FROM {source}),
             'Total on-time deliveries'),
            ('delay_rate', 'Delay Rate',
             (SELECT ROUND(CAST(SUM(CASE WHEN {delayed_col} = 1 THEN 1 ELSE 0 END) AS REAL) / COUNT(*), 4) FROM {source}),
             'Fraction of deliveries that are delayed'),
            ('severity_short', 'Severity Short Count',
             (SELECT SUM(CASE WHEN {severity_col} = 1 THEN 1 ELSE 0 END) FROM {source}),
             'Count with severity = Short (1-2h)'),
            ('severity_medium', 'Severity Medium Count',
             (SELECT SUM(CASE WHEN {severity_col} = 2 THEN 1 ELSE 0 END) FROM {source}),
             'Count with severity = Medium (3-5h)'),
            ('severity_long', 'Severity Long Count',
             (SELECT SUM(CASE WHEN {severity_col} = 3 THEN 1 ELSE 0 END) FROM {source}),
             'Count with severity = Long (6+h)'),
            ('avg_distance_km', 'Avg Distance (km)',
             (SELECT ROUND(AVG(distance_km), 2) FROM {source}),
             'Average distance in km'),
            ('avg_package_weight_kg', 'Avg Package Weight (kg)',
             (SELECT ROUND(AVG(package_weight_kg), 2) FROM {source}),
             'Average package weight in kg');
        """

    @staticmethod
    def _summary_high_risk_sql(
        table_name: str,
        prefix: str,
        delayed_col: str,
        severity_col: str,
    ) -> str:
        """Combine high-risk rows (delay_rate >= 0.30) from mode+weather, mode+distance, weather+vehicle."""
        mode_weather = f"{prefix}summary_by_mode_weather"
        mode_distance = f"{prefix}summary_by_mode_distance"
        weather_vehicle = f"{prefix}summary_by_weather_vehicle"

        return f"""
        DROP TABLE IF EXISTS {table_name};
        CREATE TABLE {table_name} AS
        SELECT 'mode_weather' AS pattern_type,
               delivery_mode || ' + ' || weather_condition AS pattern_description,
               total_deliveries, delayed_count, delay_rate,
               CASE WHEN delay_rate >= 0.5 THEN 'critical'
                    WHEN delay_rate >= 0.4 THEN 'high'
                    ELSE 'medium' END AS risk_level
        FROM {mode_weather}
        WHERE delay_rate >= 0.3
        UNION ALL
        SELECT 'mode_distance',
               delivery_mode || ' + ' || distance_category,
               total_deliveries, delayed_count, delay_rate,
               CASE WHEN delay_rate >= 0.5 THEN 'critical'
                    WHEN delay_rate >= 0.4 THEN 'high'
                    ELSE 'medium' END
        FROM {mode_distance}
        WHERE delay_rate >= 0.3
        UNION ALL
        SELECT 'weather_vehicle',
               weather_condition || ' + ' || vehicle_type,
               total_deliveries, delayed_count, delay_rate,
               CASE WHEN delay_rate >= 0.5 THEN 'critical'
                    WHEN delay_rate >= 0.4 THEN 'high'
                    ELSE 'medium' END
        FROM {weather_vehicle}
        WHERE delay_rate >= 0.3;
        """

    @classmethod
    def create_summary_tables(
        cls,
        db_path: str = "db/delivery_predictions.db",
        source_table: str = "hist_delivery_delay_prediction",
        prefix: str = "hist_",
    ) -> List[str]:
        """
        Create and populate all summary tables for a given source prediction table.

        For hist tables:  delayed_col = actual_delayed, severity_col = predict_severity
        For daily tables: delayed_col = predict_delay,   severity_col = predict_severity

        Returns list of created table names.
        """
        # Detect whether source has actual_delayed (hist) or predict_delay (daily)
        conn = sqlite3.connect(db_path)
        try:
            cols_df = pd.read_sql_query(f"PRAGMA table_info({source_table})", conn)
        finally:
            conn.close()

        source_cols = set(cols_df["name"].tolist())

        if "actual_delayed" in source_cols:
            delayed_col = "actual_delayed"
        else:
            delayed_col = "predict_delay"

        severity_col = "predict_severity"

        created_tables: List[str] = []

        # Collect all SQL blocks
        sql_blocks: List[str] = []

        # 1-6. Single-dimension summaries for each categorical column
        for cat in cls._CAT_COLS:
            tname = f"{prefix}summary_by_{cat}"
            sql_blocks.append(
                cls._summary_single_dim_sql(tname, source_table, cat, delayed_col, severity_col)
            )
            created_tables.append(tname)

        # 7. Distance category summary
        tname = f"{prefix}summary_by_distance_category"
        sql_blocks.append(
            cls._summary_distance_cat_sql(tname, source_table, delayed_col, severity_col)
        )
        created_tables.append(tname)

        # 8-10. Combined summaries
        combined_specs = [
            ("mode_weather", "delivery_mode", "weather_condition"),
            ("mode_distance", "delivery_mode", "distance_category"),
            ("weather_vehicle", "weather_condition", "vehicle_type"),
        ]
        for label, col_a, col_b in combined_specs:
            tname = f"{prefix}summary_by_{label}"
            sql_blocks.append(
                cls._summary_combined_sql(tname, source_table, col_a, col_b, delayed_col, severity_col)
            )
            created_tables.append(tname)

        # 11. Overall statistics
        tname = f"{prefix}summary_overall"
        sql_blocks.append(
            cls._summary_overall_sql(tname, source_table, delayed_col, severity_col)
        )
        created_tables.append(tname)

        # 12. High-risk patterns
        tname = f"{prefix}summary_high_risk_patterns"
        sql_blocks.append(
            cls._summary_high_risk_sql(tname, prefix, delayed_col, severity_col)
        )
        created_tables.append(tname)

        # Execute all SQL. Locked: each block is DROP TABLE IF EXISTS + CREATE TABLE,
        # two statements -- a second concurrent rebuild of the same table name must wait
        # for this one to finish, not interleave with it (see _rebuild_lock above).
        with _rebuild_lock:
            conn = sqlite3.connect(db_path)
            try:
                for i, block in enumerate(sql_blocks, 1):
                    # executescript handles multiple statements separated by ;
                    conn.executescript(block)
                    print(f"✓ Summary table {i}/{len(sql_blocks)}: {created_tables[i-1]}")
                conn.commit()
            finally:
                conn.close()

        print(f"\n✓ Created {len(created_tables)} summary tables (prefix={prefix})")
        return created_tables

    # ------------------------------------------------------------------ #
    #  3. METADATA DICTIONARY                                             #
    # ------------------------------------------------------------------ #

    # Column descriptions keyed by column name
    _COLUMN_META: Dict[str, str] = {
        # ID
        "delivery_id": "Unique shipment identifier from the source dataset.",
        # Original categorical features
        "delivery_partner": "Logistics partner handling the delivery (e.g. blue_dart, delhivery).",
        "package_type": "Category of package contents (e.g. clothing, electronics, pharmacy).",
        "vehicle_type": "Type of vehicle used (e.g. truck, bike, ev_bike, van).",
        "delivery_mode": "Service tier (e.g. same day, express, two day, standard).",
        "region": "Geographic region of delivery (e.g. north, south, east, west).",
        "weather_condition": "Weather at time of delivery (e.g. clear, rainy, stormy, foggy).",
        # Original numeric features
        "distance_km": "Delivery distance in kilometres.",
        "package_weight_kg": "Package weight in kilograms.",
        # Engineered features
        "weight_x_distance": "Interaction feature: package_weight_kg × distance_km. Captures load-distance burden.",
        "km_per_expected_hr": "Schedule tightness: distance_km / expected_time_hours. Higher = tighter schedule.",
        "cost_per_kg": "Cost efficiency: delivery_cost / package_weight_kg.",
        "weather_severity": "Ordinal encoding of weather: clear=0, hot/cold=1, foggy=2, rainy=3, stormy=4.",
        "mode_urgency": "Ordinal encoding of delivery mode urgency: standard=1, two_day=2, express=3, same_day=4.",
        "schedule_risk": "weather_severity × mode_urgency (0-16). Compounding weather-urgency pressure.",
        "vehicle_capacity": "Ordinal vehicle capacity: bike=1, ev=2, van=3, truck=4.",
        "vehicle_load_strain": "(package_weight_kg × distance_km) / vehicle_capacity. Higher = vehicle more overloaded for the route.",
        "carrier_avg_schedule": "Mean km_per_expected_hr for this delivery_partner across training data.",
        "carrier_avg_weight": "Mean package_weight_kg for this delivery_partner across training data.",
        "region_avg_distance": "Mean distance_km for this region across training data.",
        # Actuals (hist only)
        "actual_delayed": "Ground-truth binary flag: 1 = delivery was delayed, 0 = on time. (hist table only)",
        "actual_delay_hours": "Ground-truth delay duration in hours (0 if on time). (hist table only)",
        # Predictions
        "predict_delay": "Stage 1 model prediction: 1 = predicted delayed, 0 = predicted on time.",
        "predict_severity": "Two-stage severity code: 0 = No Delay, 1 = Short (1-2h), 2 = Medium (3-5h), 3 = Long (6+h).",
        "predict_severity_label": "Human-readable label for predict_severity.",
        # Summary table columns
        "total_deliveries": "Count of deliveries in this group.",
        "delayed_count": "Number of delayed deliveries in this group.",
        "on_time_count": "Number of on-time deliveries in this group.",
        "delay_rate": "Fraction of deliveries that are delayed (delayed_count / total_deliveries).",
        "severity_short_count": "Count with predict_severity = 1 (Short, 1-2h delay).",
        "severity_medium_count": "Count with predict_severity = 2 (Medium, 3-5h delay).",
        "severity_long_count": "Count with predict_severity = 3 (Long, 6+h delay).",
        "avg_distance_km": "Average distance_km in this group.",
        "avg_package_weight_kg": "Average package_weight_kg in this group.",
        "avg_schedule_risk": "Average schedule_risk in this group.",
        "distance_category": "Binned distance: short (< 50 km), medium (50-200 km), long (> 200 km).",
        "distance_range": "Human-readable distance range label.",
        "stat_id": "Unique identifier for an overall statistic row.",
        "stat_name": "Human-readable name of the statistic.",
        "stat_value": "Numeric value of the statistic.",
        "description": "Description of the statistic.",
        "pattern_type": "Type of high-risk pattern (e.g. mode_weather, weather_vehicle).",
        "pattern_description": "Human-readable description of the pattern combination.",
        "risk_level": "Risk classification: medium (30-40%), high (40-50%), critical (50%+).",
    }

    @classmethod
    def create_metadata_dictionary(
        cls,
        db_path: str = "db/delivery_predictions.db",
    ) -> pd.DataFrame:
        """
        Create a metadata_dictionary table describing every column in every table.

        Reads PRAGMA table_info for each table in the DB, joins with the
        _COLUMN_META descriptions, and writes the result.
        """
        conn = sqlite3.connect(db_path)
        try:
            # Get all user tables
            tables = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", conn
            )["name"].tolist()

            rows = []
            for tbl in tables:
                if tbl == "metadata_dictionary":
                    continue
                info = pd.read_sql_query(f"PRAGMA table_info({tbl})", conn)
                for _, col_row in info.iterrows():
                    col_name = col_row["name"]
                    col_type = col_row["type"] or "TEXT"
                    desc = cls._COLUMN_META.get(col_name, "")
                    rows.append(
                        {
                            "table_name": tbl,
                            "column_name": col_name,
                            "data_type": col_type,
                            "description": desc,
                        }
                    )

            meta_df = pd.DataFrame(rows)
            meta_df.to_sql("metadata_dictionary", conn, if_exists="replace", index=False)
            print(f"✓ metadata_dictionary: {len(meta_df)} entries across {len(tables)} tables")

        finally:
            conn.close()

        return meta_df

    # ------------------------------------------------------------------ #
    #  4. UTILITY                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    @validate_call
    def get_table(db_path: str, table_name: str, limit: int = 0) -> pd.DataFrame:
        """Read a table from the DB. limit=0 means all rows."""
        conn = sqlite3.connect(db_path)
        try:
            q = f"SELECT * FROM [{table_name}]"
            if limit > 0:
                q += f" LIMIT {int(limit)}"
            return pd.read_sql_query(q, conn)
        finally:
            conn.close()

    @staticmethod
    @validate_call
    def list_tables(db_path: str) -> List[str]:
        """Return list of table names in the database."""
        conn = sqlite3.connect(db_path)
        try:
            df = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", conn
            )
            return df["name"].tolist()
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    #  5. DIAGNOSIS — daily vs historical comparison                      #
    # ------------------------------------------------------------------ #

    # Dimensions with (label, table_suffix, group_column)
    _DIAG_DIMS = [
        ("region", "summary_by_region", "region"),
        ("weather_condition", "summary_by_weather_condition", "weather_condition"),
        ("delivery_partner", "summary_by_delivery_partner", "delivery_partner"),
        ("delivery_mode", "summary_by_delivery_mode", "delivery_mode"),
        ("package_type", "summary_by_package_type", "package_type"),
        ("vehicle_type", "summary_by_vehicle_type", "vehicle_type"),
        ("distance_category", "summary_by_distance_category", "distance_category"),
    ]

    @classmethod
    def get_diagnosis_data(cls, db_path: str) -> Dict:
        """Compare daily vs historical summaries across all dimensions.

        Returns a dict with:
          - overall_daily / overall_hist: KPIs from the overall summary tables
          - comparison: merged rows for every dimension (daily vs hist delay rates)
          - daily_high_risk_patterns / hist_high_risk_patterns
        """
        def _read_overall(prefix: str) -> dict:
            try:
                df = cls.get_table(db_path, f"{prefix}summary_overall")
                return {r["stat_id"]: r["stat_value"] for _, r in df.iterrows()}
            except Exception:
                return {}

        def _read_high_risk(prefix: str) -> List[dict]:
            try:
                df = cls.get_table(db_path, f"{prefix}summary_high_risk_patterns")
                df["delay_rate_pct"] = (df["delay_rate"] * 100).round(2)
                return df[["pattern_type", "pattern_description",
                            "total_deliveries", "delayed_count",
                            "delay_rate_pct", "risk_level"]].to_dict(orient="records")
            except Exception:
                return []

        comparison_rows: List[dict] = []
        for dim_label, table_suffix, group_col in cls._DIAG_DIMS:
            try:
                daily = cls.get_table(db_path, f"daily_{table_suffix}")
                hist = cls.get_table(db_path, f"hist_{table_suffix}")
            except Exception:
                continue
            merged = pd.merge(
                daily[[group_col, "total_deliveries", "delayed_count", "delay_rate"]],
                hist[[group_col, "total_deliveries", "delayed_count", "delay_rate"]],
                on=group_col,
                suffixes=("_daily", "_hist"),
                how="outer",
            ).fillna(0)
            for _, r in merged.iterrows():
                d_rate = round(float(r["delay_rate_daily"]) * 100, 2)
                h_rate = round(float(r["delay_rate_hist"]) * 100, 2)
                comparison_rows.append({
                    "dimension": dim_label,
                    "category": str(r[group_col]),
                    "daily_total": int(r["total_deliveries_daily"]),
                    "daily_delayed": int(r["delayed_count_daily"]),
                    "daily_delay_rate_pct": d_rate,
                    "hist_total": int(r["total_deliveries_hist"]),
                    "hist_delayed": int(r["delayed_count_hist"]),
                    "hist_delay_rate_pct": h_rate,
                    "rate_change_pct": round(d_rate - h_rate, 2),
                })

        return {
            "overall_daily": _read_overall("daily_"),
            "overall_hist": _read_overall("hist_"),
            "comparison": comparison_rows,
            "daily_high_risk_patterns": _read_high_risk("daily_"),
            "hist_high_risk_patterns": _read_high_risk("hist_"),
        }

