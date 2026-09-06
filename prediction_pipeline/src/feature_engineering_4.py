"""
Feature Engineering Module

This module contains functions for feature engineering operations including
pre-split, data splitting, and post-split feature engineering pipelines.

Engineered Features
===================

Pre-split (created before train/test split to avoid leakage in derivation):

  Target / derived-target columns:
    delay_hours        – Continuous regression target. Derived from slack_time:
                         0 when on-time (slack_time >= 0), abs(slack_time) when late.
                         Inconsistent rows (delayed=1 but delay_hours=0) are imputed
                         with group-based median (delivery_mode × weather_condition),
                         falling back to global median.
    delayed            – Binary classification target (1 = delayed, 0 = on-time).
                         Encoded from raw 'yes'/'no' strings.

  Interaction features (capture non-linear relationships trees can't derive):
    weight_x_distance  – package_weight_kg × distance_km.
                         Heavy parcels travelling long distances are more delay-prone
                         due to handling complexity and route constraints.
    km_per_expected_hr – distance_km / (expected_time_hrs + ε).
                         Schedule tightness: high values indicate aggressive delivery
                         windows relative to distance — strongest correlation with
                         delay_hours (r ≈ 0.59).
    cost_per_km        – delivery_cost / (distance_km + ε).
                         Under-priced long-haul deliveries may signal under-resourced
                         routes (low priority, fewer vehicles).
    cost_per_kg        – delivery_cost / (package_weight_kg + ε).
                         Effectively a distance-to-weight ratio (since cost ≈ k × distance).
                         Low values flag heavy shipments on cheap routes.

  Ordinal / risk features (encode domain knowledge as numeric signals):
    weather_severity   – Ordinal encoding of weather_condition:
                         Clear=0, Hot/Cold=1, Foggy=2, Rainy=3, Stormy=4.
                         Gives models a monotonic severity axis instead of treating
                         weather categories as unordered labels.
    mode_urgency       – Ordinal encoding of delivery_mode:
                         Standard=1, Two Day=2, Express=3, Same Day=4.
                         Higher urgency modes have tighter SLAs, so even small
                         disruptions translate into delays.
    schedule_risk      – weather_severity × mode_urgency.
                         Combined risk score: a Same-Day delivery in a Storm (4×4=16)
                         is far riskier than a Standard delivery on a Clear day (0×1=0).
                         Captures the interaction without inflating dimensionality
                         via full categorical cross-encoding.
    vehicle_capacity   – Ordinal encoding of vehicle_type by carrying capacity:
                         Bike=1, EV=2, Van=3, Truck=4.
                         Lets the model treat vehicle size as a continuous scale.
    vehicle_load_strain – (package_weight_kg × distance_km) / vehicle_capacity.
                         Measures how overloaded a vehicle is relative to its
                         capacity over the delivery distance. A bike carrying
                         heavy cargo over a long distance gets a very high score.

  Group-aggregate features (concentrate carrier & region signals):
    carrier_avg_schedule – Mean km_per_expected_hr per delivery_partner.
                           Carriers that routinely accept tight schedules are
                           riskier; this captures their operational aggressiveness.
    carrier_avg_weight   – Mean package_weight_kg per delivery_partner.
                           Carriers handling heavier loads face more logistics
                           friction (loading times, routing constraints).
    region_avg_distance  – Mean distance_km per region.
                           Regions with longer average routes have more delay
                           exposure due to cumulative transit risk.
    (dropped) region_avg_schedule — r=0.93 with region_avg_distance (redundant).
    Note: All three are computed from non-target columns only (no leakage).
    They use groupby(...).transform('mean'), so every row gets the group stat.

Post-split (applied separately to train and test to prevent data leakage):
    One-hot encoding   – Categorical columns (delivery_partner, package_type,
                         vehicle_type, delivery_mode, region, weather_condition)
                         are one-hot encoded with drop_first=True.
    Standard scaling   – Numeric features are z-score normalised using statistics
                         fitted on the training set only.
"""

# Lazy annotations so the StandardScaler type hints below don't require sklearn to be
# importable at module load time -- see the local imports inside train_test_split_data
# and scale_features for why: those are the only two places in this file that touch
# sklearn, and both are training-only (daily_predict.py's actual inference call only
# ever reaches feature_eng_pre_split_pipeline, never these). Diagnosed 5-Sep-26:
# importing sklearn eagerly here forced the MCP server's startup (prediction_server.py)
# to pay sklearn's own __check_build compiled-extension load on every run, for every
# query, even ones that never touch predict_delivery_delays at all.
from __future__ import annotations

import pandas as pd
import numpy as np
from typing import List, Optional, Tuple
from .data_processing_3 import DataProcessing

class FeatureEngineering:
    """Class for feature engineering operations."""
    
    @staticmethod
    def create_delay_hours(df: pd.DataFrame, 
                           slack_time_column: str = 'slack_time',
                           delay_hours_column: str = 'delay_hours',
                           display: bool = True) -> pd.DataFrame:
        """
        Create delay_hours column based on slack_time.
        If slack_time >= 0, delay_hours = 0
        If slack_time < 0, delay_hours = abs(slack_time)
        
        Parameters:
        df: pandas DataFrame
        slack_time_column: str, name of the slack_time column (default: 'slack_time')
        delay_hours_column: str, name of the new delay_hours column to create (default: 'delay_hours')
        display: bool, whether to print information about the operation (default: True)
        
        Returns:
        df: DataFrame with delay_hours column added
        """
        df_eng = df.copy()
        
        if slack_time_column not in df_eng.columns:
            if display:
                print(f"Error: Column '{slack_time_column}' not found in dataframe.")
            return df_eng
        
        if display:
            print(f"Creating '{delay_hours_column}' column from '{slack_time_column}'...")
            print(f"  Rule: delay_hours = 0 if {slack_time_column} >= 0, else delay_hours = abs({slack_time_column})")
        
        # Create delay_hours column
        df_eng[delay_hours_column] = df_eng[slack_time_column].apply(
            lambda x: 0 if x >= 0 else abs(x)
        )
        
        if display:
            print(f"  ✓ Column '{delay_hours_column}' created successfully")
            print(f"  Statistics:")
            print(f"    Total records: {len(df_eng)}")
            print(f"    Records with delay (delay_hours > 0): {(df_eng[delay_hours_column] > 0).sum()} ({(df_eng[delay_hours_column] > 0).sum() / len(df_eng) * 100:.2f}%)")
            print(f"    Records without delay (delay_hours = 0): {(df_eng[delay_hours_column] == 0).sum()} ({(df_eng[delay_hours_column] == 0).sum() / len(df_eng) * 100:.2f}%)")
            if (df_eng[delay_hours_column] > 0).sum() > 0:
                print(f"    Delay hours - Min: {df_eng[df_eng[delay_hours_column] > 0][delay_hours_column].min():.2f}, "
                      f"Max: {df_eng[df_eng[delay_hours_column] > 0][delay_hours_column].max():.2f}, "
                      f"Mean: {df_eng[df_eng[delay_hours_column] > 0][delay_hours_column].mean():.2f}")
            print(f"  Dataset shape: {df_eng.shape}")
        
        return df_eng
    
    @staticmethod
    def create_interaction_features(df: pd.DataFrame,
                                     distance_col: str = 'distance_km',
                                     weight_col: str = 'package_weight_kg',
                                     cost_col: str = 'delivery_cost',
                                     expected_time_col: str = 'expected_time_hrs_num',
                                     display: bool = True) -> Tuple[pd.DataFrame, List[str]]:
        """
        Create interaction features that capture relationships between raw features.
        These help tree-based models find better splits.
        
        New features:
        - weight_x_distance: heavy + far = more delay-prone
        - km_per_expected_hr: schedule tightness (high = aggressive schedule)
        - cost_per_km: under-priced long deliveries may be under-resourced
        - cost_per_kg: under-priced heavy packages may get deprioritized
        
        Parameters:
        df: pandas DataFrame
        distance_col, weight_col, cost_col, expected_time_col: column names
        display: whether to print info
        
        Returns:
        df_eng: DataFrame with interaction features added
        new_columns: list of new column names created
        """
        df_eng = df.copy()
        new_columns = []
        eps = 0.1  # avoid division by zero

        required = [distance_col, weight_col, cost_col, expected_time_col]
        missing = [c for c in required if c not in df_eng.columns]
        if missing:
            if display:
                print(f"  Warning: Missing columns for interaction features: {missing}")
            available = [c for c in required if c in df_eng.columns]
        else:
            available = required

        if distance_col in df_eng.columns and weight_col in df_eng.columns:
            df_eng['weight_x_distance'] = df_eng[weight_col] * df_eng[distance_col]
            new_columns.append('weight_x_distance')

        if distance_col in df_eng.columns and expected_time_col in df_eng.columns:
            df_eng['km_per_expected_hr'] = df_eng[distance_col] / (df_eng[expected_time_col] + eps)
            new_columns.append('km_per_expected_hr')

        if cost_col in df_eng.columns and distance_col in df_eng.columns:
            df_eng['cost_per_km'] = df_eng[cost_col] / (df_eng[distance_col] + eps)
            new_columns.append('cost_per_km')

        if cost_col in df_eng.columns and weight_col in df_eng.columns:
            df_eng['cost_per_kg'] = df_eng[cost_col] / (df_eng[weight_col] + eps)
            new_columns.append('cost_per_kg')

        if display:
            print(f"  ✓ Created {len(new_columns)} interaction features:")
            formulas = {
                'weight_x_distance': f'{weight_col} × {distance_col}',
                'km_per_expected_hr': f'{distance_col} / ({expected_time_col} + {eps})',
                'cost_per_km': f'{cost_col} / ({distance_col} + {eps})',
                'cost_per_kg': f'{cost_col} / ({weight_col} + {eps})',
            }
            for col in new_columns:
                formula = formulas.get(col, '—')
                print(f"    • {col} = {formula}")
                print(f"      min={df_eng[col].min():.2f}, max={df_eng[col].max():.2f}, "
                      f"mean={df_eng[col].mean():.2f}, std={df_eng[col].std():.2f}")
            print(f"  Dataset shape: {df_eng.shape}")

        return df_eng, new_columns

    @staticmethod
    def create_ordinal_features(df: pd.DataFrame,
                                weather_col: str = 'weather_condition',
                                mode_col: str = 'delivery_mode',
                                vehicle_col: str = 'vehicle_type',
                                weight_col: str = 'package_weight_kg',
                                distance_col: str = 'distance_km',
                                display: bool = True) -> Tuple[pd.DataFrame, List[str]]:
        """
        Create ordinal-encoded features and derived interactions.
        - weather_severity: Clear=0, Hot/Cold=1, Foggy=2, Rainy=3, Stormy=4
        - mode_urgency: Standard=1, Two Day=2, Express=3, Same Day=4
        - schedule_risk: weather_severity × mode_urgency
        - vehicle_capacity: Bike=1, EV=2, Van=3, Truck=4 (by carrying capacity)
        - vehicle_load_strain: (weight × distance) / vehicle_capacity
          High values = small vehicle hauling heavy cargo over long distance
        """
        df_eng = df.copy()
        new_columns = []

        weather_map = {
            'clear': 0, 'hot': 1, 'cold': 1,
            'foggy': 2, 'rainy': 3, 'stormy': 4,
        }
        mode_map = {
            'standard': 1, 'two day': 2, 'express': 3, 'same day': 4,
        }
        vehicle_map = {
            'bike': 1, 'ev': 2, 'van': 3, 'truck': 4,
        }

        if weather_col in df_eng.columns:
            df_eng['weather_severity'] = (df_eng[weather_col]
                                          .str.lower().str.strip()
                                          .map(weather_map).fillna(0).astype(int))
            new_columns.append('weather_severity')

        if mode_col in df_eng.columns:
            df_eng['mode_urgency'] = (df_eng[mode_col]
                                      .str.lower().str.strip()
                                      .map(mode_map).fillna(1).astype(int))
            new_columns.append('mode_urgency')

        if 'weather_severity' in df_eng.columns and 'mode_urgency' in df_eng.columns:
            df_eng['schedule_risk'] = df_eng['weather_severity'] * df_eng['mode_urgency']
            new_columns.append('schedule_risk')

        if vehicle_col in df_eng.columns:
            df_eng['vehicle_capacity'] = (df_eng[vehicle_col]
                                          .str.lower().str.strip()
                                          .map(vehicle_map).fillna(2).astype(int))
            new_columns.append('vehicle_capacity')

            if weight_col in df_eng.columns and distance_col in df_eng.columns:
                df_eng['vehicle_load_strain'] = (
                    (df_eng[weight_col] * df_eng[distance_col])
                    / df_eng['vehicle_capacity']
                )
                new_columns.append('vehicle_load_strain')

        if display:
            print(f"  ✓ Created {len(new_columns)} ordinal/derived features:")
            formulas = {
                'weather_severity': f'{weather_col} → Clear=0, Hot/Cold=1, Foggy=2, Rainy=3, Stormy=4',
                'mode_urgency': f'{mode_col} → Standard=1, Two Day=2, Express=3, Same Day=4',
                'schedule_risk': 'weather_severity × mode_urgency',
                'vehicle_capacity': f'{vehicle_col} → Bike=1, EV=2, Van=3, Truck=4',
                'vehicle_load_strain': f'({weight_col} × {distance_col}) / vehicle_capacity',
            }
            for col in new_columns:
                formula = formulas.get(col, '')
                print(f"    • {col} = {formula}")
                vals = df_eng[col]
                print(f"      min={vals.min():.2f}, max={vals.max():.2f}, "
                      f"mean={vals.mean():.2f}, std={vals.std():.2f}")
            print(f"  Dataset shape: {df_eng.shape}")

        return df_eng, new_columns

    @staticmethod
    def create_group_aggregate_features(df: pd.DataFrame,
                                        carrier_col: str = 'delivery_partner',
                                        region_col: str = 'region',
                                        display: bool = True) -> Tuple[pd.DataFrame, List[str]]:
        """
        Create aggregate features per carrier and region using non-target columns only
        (no leakage). Each record gets the group-level statistic for its carrier/region.

        Features created:
          carrier_avg_schedule – mean km_per_expected_hr per carrier.
                                Carriers that routinely accept tight schedules are riskier.
          carrier_avg_weight   – mean package_weight_kg per carrier.
                                Carriers that handle heavier loads face more logistics friction.
          region_avg_distance  – mean distance_km per region.
                                Regions with longer average routes have more delay exposure.

        Dropped:
          region_avg_schedule  – r=0.93 with region_avg_distance (redundant).
        """
        df_eng = df.copy()
        new_columns = []

        feature_specs = [
            (carrier_col, 'km_per_expected_hr', 'mean', 'carrier_avg_schedule'),
            (carrier_col, 'package_weight_kg',  'mean', 'carrier_avg_weight'),
            (region_col,  'distance_km',        'mean', 'region_avg_distance'),
        ]

        for group_col, value_col, agg_func, new_col in feature_specs:
            if group_col not in df_eng.columns or value_col not in df_eng.columns:
                if display:
                    print(f"  Skipping {new_col}: missing {group_col} or {value_col}")
                continue

            group_agg = df_eng.groupby(group_col)[value_col].transform(agg_func)
            df_eng[new_col] = group_agg.round(4)
            new_columns.append(new_col)

        if display:
            print(f"  ✓ Created {len(new_columns)} group-aggregate features:")
            for col in new_columns:
                print(f"    • {col}")
                print(f"      min={df_eng[col].min():.4f}, max={df_eng[col].max():.4f}, "
                      f"mean={df_eng[col].mean():.4f}, nunique={df_eng[col].nunique()}")
            print(f"  Dataset shape: {df_eng.shape}")

        return df_eng, new_columns

    @staticmethod
    def encode_delayed_column(df: pd.DataFrame,
                              delayed_column: str = 'delayed',
                              display: bool = True) -> pd.DataFrame:
        """
        Map 'yes'/'no' values in delayed column to 1/0 (case insensitive).
        
        Parameters:
        df: pandas DataFrame
        delayed_column: str, name of the delayed column (default: 'delayed')
        display: bool, whether to print information about the operation (default: True)
        
        Returns:
        df: DataFrame with delayed column encoded to 0/1
        """
        df_eng = df.copy()
        
        if delayed_column not in df_eng.columns:
            if display:
                print(f"Error: Column '{delayed_column}' not found in dataframe.")
            return df_eng
        
        if display:
            print(f"Encoding '{delayed_column}' column (yes/no -> 1/0, case insensitive)...")
            print(f"  Value counts before encoding:")
            print(df_eng[delayed_column].value_counts())
        
        # Map yes/no to 1/0 (case insensitive)
        df_eng[delayed_column] = df_eng[delayed_column].str.lower().str.strip().map({
            'yes': 1,
            'no': 0
        })
        
        # Check for any unmapped values
        unmapped = df_eng[delayed_column].isna().sum()
        if unmapped > 0:
            if display:
                print(f"  ⚠️  Warning: {unmapped} records have unmapped values (not 'yes' or 'no')")
                unmapped_values = df[delayed_column][df_eng[delayed_column].isna()].unique()
                print(f"     Unmapped values: {unmapped_values}")
        
        if display:
            print(f"  ✓ Column '{delayed_column}' encoded successfully")
            print(f"  Value counts after encoding:")
            print(df_eng[delayed_column].value_counts())
            print(f"  Dataset shape: {df_eng.shape}")
        
        return df_eng
    
    @staticmethod
    def analyze_delay_consistency(df: pd.DataFrame,
                                  delay_hours_column: str = 'delay_hours',
                                  delayed_column: str = 'delayed',
                                  display: bool = True) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Check if all records with delay_hours > 0 also have delayed = 1, and analyze differences.
        If delay_hours = 0 BUT delayed = 1, imputes delay_hours using group-based median
        (by delivery_mode + weather_condition), falling back to global median.
        
        Parameters:
        df: pandas DataFrame
        delay_hours_column: str, name of the delay_hours column (default: 'delay_hours')
        delayed_column: str, name of the delayed column (default: 'delayed')
        display: bool, whether to print analysis results (default: True)
        
        Returns:
        df_updated: DataFrame with inconsistencies fixed (delay_hours updated where needed)
        analysis_df: DataFrame with analysis results showing inconsistencies
        """
        df_updated = df.copy()
        
        if delay_hours_column not in df_updated.columns:
            if display:
                print(f"Error: Column '{delay_hours_column}' not found in dataframe.")
            return df_updated, pd.DataFrame()
        
        if delayed_column not in df_updated.columns:
            if display:
                print(f"Error: Column '{delayed_column}' not found in dataframe.")
            return df_updated, pd.DataFrame()
        
        if display:
            print("=" * 80)
            print("DELAY CONSISTENCY ANALYSIS")
            print("=" * 80)
            print(f"\nAnalyzing consistency between '{delay_hours_column}' and '{delayed_column}'...")
        
        # Create analysis
        total_records = len(df_updated)
        records_with_delay_hours = (df_updated[delay_hours_column] > 0).sum()
        records_with_delayed_1 = (df_updated[delayed_column] == 1).sum()
        
        # Cases to analyze
        case1 = (df_updated[delay_hours_column] > 0) & (df_updated[delayed_column] == 1)  # Both indicate delay
        case2 = (df_updated[delay_hours_column] > 0) & (df_updated[delayed_column] == 0)  # delay_hours > 0 but delayed = 0 (inconsistency)
        case3 = (df_updated[delay_hours_column] == 0) & (df_updated[delayed_column] == 1)  # delay_hours = 0 but delayed = 1 (inconsistency)
        case4 = (df_updated[delay_hours_column] == 0) & (df_updated[delayed_column] == 0)  # Both indicate no delay
        
        count1 = case1.sum()
        count2 = case2.sum()
        count3 = case3.sum()
        count4 = case4.sum()
        
        if display:
            print(f"\nTotal records: {total_records}")
            print(f"Records with {delay_hours_column} > 0: {records_with_delay_hours} ({records_with_delay_hours/total_records*100:.2f}%)")
            print(f"Records with {delayed_column} = 1: {records_with_delayed_1} ({records_with_delayed_1/total_records*100:.2f}%)")
            
            print(f"\nConsistency Analysis:")
            print(f"  ✓ Consistent - Both indicate delay ({delay_hours_column} > 0 AND {delayed_column} = 1): {count1} ({count1/total_records*100:.2f}%)")
            print(f"  ✓ Consistent - Both indicate no delay ({delay_hours_column} = 0 AND {delayed_column} = 0): {count4} ({count4/total_records*100:.2f}%)")
            print(f"  ⚠️  Inconsistent - {delay_hours_column} > 0 BUT {delayed_column} = 0: {count2} ({count2/total_records*100:.2f}%)")
            print(f"  ⚠️  Inconsistent - {delay_hours_column} = 0 BUT {delayed_column} = 1: {count3} ({count3/total_records*100:.2f}%)")
            
            total_inconsistent = count2 + count3
            consistency_rate = ((count1 + count4) / total_records * 100) if total_records > 0 else 0
            print(f"\n  Overall Consistency Rate: {consistency_rate:.2f}%")
            print(f"  Total Inconsistencies: {total_inconsistent} ({total_inconsistent/total_records*100:.2f}%)")
            
            # Show sample inconsistent records
            if count2 > 0:
                print(f"\n  Sample records where {delay_hours_column} > 0 but {delayed_column} = 0:")
                inconsistent_df = df_updated[case2][[delay_hours_column, delayed_column]].head(10)
                print(inconsistent_df.to_string())
            
            if count3 > 0:
                print(f"\n  Sample records where {delay_hours_column} = 0 but {delayed_column} = 1:")
                inconsistent_df = df_updated[case3][[delay_hours_column, delayed_column]].head(10)
                print(inconsistent_df.to_string())
        
        # Fix inconsistency: delay_hours = 0 BUT delayed = 1
        # Use group-based median (delivery_mode + weather_condition) for more realistic imputation
        if count3 > 0:
            delayed_only = df_updated[df_updated[delay_hours_column] > 0]
            global_median = delayed_only[delay_hours_column].median()

            group_cols = ['delivery_mode', 'weather_condition']
            available_groups = [c for c in group_cols if c in df_updated.columns]

            if available_groups and not pd.isna(global_median):
                group_medians = delayed_only.groupby(available_groups)[delay_hours_column].median()

                if display:
                    print(f"\n  Fixing inconsistency: {delay_hours_column} = 0 BUT {delayed_column} = 1")
                    print(f"    Strategy: group-based median by {available_groups}")
                    print(f"    Global median (fallback): {global_median:.2f}")
                    print(f"    Group medians:")
                    for grp, val in group_medians.items():
                        print(f"      {grp}: {val:.2f}")

                # Vectorised: merge group medians, fill missing groups with global median
                inconsistent_df = df_updated.loc[case3, available_groups].copy()
                merged = inconsistent_df.merge(
                    group_medians.rename('_grp_median').reset_index(),
                    on=available_groups, how='left'
                )
                fill_values = merged['_grp_median'].fillna(global_median)
                filled_count = int(merged['_grp_median'].notna().sum())
                fallback_count = int(merged['_grp_median'].isna().sum())
                df_updated.loc[case3, delay_hours_column] = fill_values.values

                if display:
                    print(f"    ✓ Updated {filled_count} records using group median")
                    if fallback_count > 0:
                        print(f"    ✓ Updated {fallback_count} records using global median (group not found)")

            elif not pd.isna(global_median):
                if display:
                    print(f"\n  Fixing inconsistency: {delay_hours_column} = 0 BUT {delayed_column} = 1")
                    print(f"    Group columns not available, using global median: {global_median:.2f}")
                df_updated.loc[case3, delay_hours_column] = global_median
                if display:
                    print(f"    ✓ Updated {count3} records")
            else:
                if display:
                    print(f"\n  Cannot fix: No records with {delay_hours_column} > 0 to calculate median")
        
        # Create analysis DataFrame
        analysis_data = {
            'Case': [
                f'{delay_hours_column} > 0 AND {delayed_column} = 1 (Consistent - Both indicate delay)',
                f'{delay_hours_column} = 0 AND {delayed_column} = 0 (Consistent - Both indicate no delay)',
                f'{delay_hours_column} > 0 BUT {delayed_column} = 0 (Inconsistent)',
                f'{delay_hours_column} = 0 BUT {delayed_column} = 1 (Inconsistent - Fixed)'
            ],
            'Count': [count1, count4, count2, count3],
            'Percentage': [
                count1/total_records*100 if total_records > 0 else 0,
                count4/total_records*100 if total_records > 0 else 0,
                count2/total_records*100 if total_records > 0 else 0,
                count3/total_records*100 if total_records > 0 else 0
            ]
        }
        analysis_df = pd.DataFrame(analysis_data)
        
        if display:
            print("\n" + "=" * 80)
        
        return df_updated, analysis_df
    
    @staticmethod
    def split_X_y(df: pd.DataFrame, 
                  feature_columns: List[str], 
                  target_column: str,
                  display: bool = True) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Split dataframe into X (features) and y (target).
        
        Parameters:
        df: pandas DataFrame
        feature_columns: List[str], list of column names to use as features (X)
        target_column: str, name of the target column (y)
        display: bool, whether to print information (default: True)
        
        Returns:
        X: DataFrame with feature columns
        y: Series with target column
        """
        if target_column not in df.columns:
            raise ValueError(f"Target column '{target_column}' not found in dataframe.")
        
        missing_features = [col for col in feature_columns if col not in df.columns]
        if missing_features:
            raise ValueError(f"Feature columns not found in dataframe: {missing_features}")
        
        X = df[feature_columns].copy()
        y = df[target_column].copy()
        
        if display:
            print("=" * 80)
            print("SPLIT X AND Y")
            print("=" * 80)
            print(f"\nTotal records: {len(df)}")
            print(f"Number of features: {len(feature_columns)}")
            print(f"Feature columns: {feature_columns}")
            print(f"Target column: {target_column}")
            print(f"\nX shape: {X.shape}")
            print(f"y shape: {y.shape}")
            print("=" * 80)
        
        return X, y
    
    @staticmethod
    def train_test_split_data(X: pd.DataFrame, 
                              y: pd.Series,
                              test_size: float = 0.2,
                              random_state: int = 42,
                              stratify: Optional[pd.Series] = None,
                              display: bool = True) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
        """
        Split data into training and testing sets.
        
        Parameters:
        X: pandas DataFrame, features
        y: pandas Series, target
        test_size: float, proportion of dataset to include in test split (default: 0.2)
        random_state: int, random seed for reproducibility (default: 42)
        stratify: Optional[pd.Series], array-like object used for stratified splitting.
                 If provided, ensures train and test sets have same class distribution (default: None)
        display: bool, whether to print information (default: True)
        
        Returns:
        X_train: DataFrame, training features
        X_test: DataFrame, testing features
        y_train: Series, training target
        y_test: Series, testing target
        """
        from sklearn.model_selection import train_test_split  # training-only, see module note

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, 
            test_size=test_size, 
            random_state=random_state,
            shuffle=True,
            stratify=stratify
        )
        
        if display:
            print("=" * 80)
            print("TRAIN-TEST SPLIT")
            print("=" * 80)
            print(f"\nSplit ratio: {(1-test_size)*100:.0f}% train / {test_size*100:.0f}% test")
            print(f"Random state: {random_state}")
            if stratify is not None:
                print(f"Stratify: Yes (ensures same class distribution in train/test)")
            else:
                print(f"Stratify: No")
            print(f"\nX_train shape: {X_train.shape}")
            print(f"X_test shape: {X_test.shape}")
            print(f"y_train shape: {y_train.shape}")
            print(f"y_test shape: {y_test.shape}")
            print("=" * 80)
        
        return X_train, X_test, y_train, y_test
    
    @staticmethod
    def scale_features(X_train: pd.DataFrame,
                      X_test: pd.DataFrame,
                      numeric_columns: Optional[List[str]] = None,
                      display: bool = True) -> Tuple[pd.DataFrame, pd.DataFrame, StandardScaler]:
        """
        Scale numeric features using StandardScaler (fit on train, transform on test).
        
        Parameters:
        X_train: pandas DataFrame, training features
        X_test: pandas DataFrame, testing features
        numeric_columns: Optional[List[str]], list of numeric columns to scale. 
                        If None, automatically detects numeric columns (default: None)
        display: bool, whether to print information (default: True)
        
        Returns:
        X_train_scaled: DataFrame, scaled training features
        X_test_scaled: DataFrame, scaled testing features
        scaler: StandardScaler, fitted scaler object
        """
        from sklearn.preprocessing import StandardScaler  # training-only, see module note

        X_train_scaled = X_train.copy()
        X_test_scaled = X_test.copy()

        # Auto-detect numeric columns if not provided
        if numeric_columns is None:
            numeric_columns = X_train.select_dtypes(include=[np.number]).columns.tolist()

        # Remove any columns that don't exist in the dataframes
        numeric_columns = [col for col in numeric_columns if col in X_train.columns]

        if not numeric_columns:
            if display:
                print("Warning: No numeric columns found to scale.")
            return X_train_scaled, X_test_scaled, None

        # Initialize and fit scaler on training data
        scaler = StandardScaler()
        X_train_scaled[numeric_columns] = scaler.fit_transform(X_train[numeric_columns])
        
        # Transform test data using the fitted scaler
        X_test_scaled[numeric_columns] = scaler.transform(X_test[numeric_columns])
        
        if display:
            print("=" * 80)
            print("FEATURE SCALING")
            print("=" * 80)
            print(f"\nScaled columns: {numeric_columns}")
            print(f"Number of columns scaled: {len(numeric_columns)}")
            print(f"\nX_train_scaled shape: {X_train_scaled.shape}")
            print(f"X_test_scaled shape: {X_test_scaled.shape}")
            print("\nScaler statistics (from training data):")
            for i, col in enumerate(numeric_columns):
                print(f"  {col}: mean={scaler.mean_[i]:.4f}, std={scaler.scale_[i]:.4f}")
            print("=" * 80)
        
        return X_train_scaled, X_test_scaled, scaler
    
    @staticmethod
    def one_hot_encode(X_train: pd.DataFrame,
                      X_test: pd.DataFrame,
                      categorical_columns: Optional[List[str]] = None,
                      drop_first: bool = True,
                      prefix_separator: str = "_",
                      display: bool = True) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
        """
        Perform one-hot encoding on categorical columns with standardized naming convention.
        
        Parameters:
        X_train: pandas DataFrame, training features
        X_test: pandas DataFrame, testing features
        categorical_columns: Optional[List[str]], list of categorical columns to encode.
                            If None, automatically detects object/category columns (default: None)
        drop_first: bool, whether to drop the first category to avoid multicollinearity (default: False)
        prefix_separator: str, separator between column name and category value (default: "_")
        display: bool, whether to print information (default: True)
        
        Returns:
        X_train_encoded: DataFrame, one-hot encoded training features
        X_test_encoded: DataFrame, one-hot encoded testing features
        encoded_columns: List[str], list of newly created one-hot encoded column names
        """
        X_train_encoded = X_train.copy()
        X_test_encoded = X_test.copy()
        
        # Auto-detect categorical columns if not provided
        if categorical_columns is None:
            categorical_columns = X_train.select_dtypes(include=['object', 'category']).columns.tolist()
        
        # Remove any columns that don't exist in the dataframes
        categorical_columns = [col for col in categorical_columns if col in X_train.columns]
        
        if not categorical_columns:
            if display:
                print("Warning: No categorical columns found to encode.")
            return X_train_encoded, X_test_encoded, []
        
        encoded_columns = []
        
        # Get all unique categories from training data to ensure consistency
        all_categories = {}
        for col in categorical_columns:
            all_categories[col] = X_train[col].unique()
        
        # Perform one-hot encoding
        for col in categorical_columns:
            # Get dummies for training data
            train_dummies = pd.get_dummies(
                X_train[col], 
                prefix=col, 
                prefix_sep=prefix_separator,
                drop_first=drop_first,
                dtype=int
            )
            
            # Get dummies for test data (using same categories as training)
            test_dummies = pd.get_dummies(
                X_test[col], 
                prefix=col, 
                prefix_sep=prefix_separator,
                drop_first=drop_first,
                dtype=int
            )
            
            # Ensure test data has same columns as training (add missing columns with 0s)
            for train_col in train_dummies.columns:
                if train_col not in test_dummies.columns:
                    test_dummies[train_col] = 0
            
            # Remove any columns in test that weren't in training
            test_dummies = test_dummies[train_dummies.columns]
            
            # Drop original column and add encoded columns
            X_train_encoded = X_train_encoded.drop(columns=[col])
            X_test_encoded = X_test_encoded.drop(columns=[col])
            
            X_train_encoded = pd.concat([X_train_encoded, train_dummies], axis=1)
            X_test_encoded = pd.concat([X_test_encoded, test_dummies], axis=1)
            
            encoded_columns.extend(train_dummies.columns.tolist())
        
        if display:
            print("=" * 80)
            print("ONE-HOT ENCODING")
            print("=" * 80)
            print(f"\nEncoded columns: {categorical_columns}")
            print(f"Number of original categorical columns: {len(categorical_columns)}")
            print(f"Number of new encoded columns: {len(encoded_columns)}")
            print(f"Drop first category: {drop_first}")
            print(f"Prefix separator: '{prefix_separator}'")
            print(f"\nX_train_encoded shape: {X_train_encoded.shape}")
            print(f"X_test_encoded shape: {X_test_encoded.shape}")
            print(f"\nNew encoded column names (first 20):")
            for i, col in enumerate(encoded_columns[:20]):
                print(f"  {col}")
            if len(encoded_columns) > 20:
                print(f"  ... and {len(encoded_columns) - 20} more")
            print("=" * 80)
        
        return X_train_encoded, X_test_encoded, encoded_columns
    
    @staticmethod
    def feature_eng_pre_split_pipeline(df: pd.DataFrame,
                              slack_time_column: str = 'slack_time',
                              delay_hours_column: str = 'delay_hours',
                              delayed_column: str = 'delayed',
                              display: bool = True) -> pd.DataFrame:
        """
        Pipeline for feature engineering before data split.
        Creates delay_hours, encodes delayed column, and analyzes consistency.
        
        Parameters:
        df: pandas DataFrame, input dataset
        slack_time_column: str, name of slack_time column (default: 'slack_time')
        delay_hours_column: str, name of delay_hours column to create (default: 'delay_hours')
        delayed_column: str, name of delayed column (default: 'delayed')
        display: bool, whether to print information (default: True)
        
        Returns:
        df_eng: DataFrame with feature engineering applied
        """
        if display:
            print("=" * 80)
            print("FEATURE ENGINEERING PRE-SPLIT PIPELINE")
            print("=" * 80)
            print()
        
        # Step 1: Create delay_hours column
        if display:
            print("Step 1: Creating delay_hours column...")
        df_eng = FeatureEngineering.create_delay_hours(
            df,
            slack_time_column=slack_time_column,
            delay_hours_column=delay_hours_column,
            display=display
        )
        if display:
            print()
        
        # Step 2: Encode delayed column
        if display:
            print("Step 2: Encoding delayed column...")
        df_eng = FeatureEngineering.encode_delayed_column(
            df_eng,
            delayed_column=delayed_column,
            display=display
        )
        if display:
            print()
        
        # Step 3: Analyze delay consistency and fix inconsistencies
        if display:
            print("Step 3: Analyzing delay consistency...")
        df_eng, _ = FeatureEngineering.analyze_delay_consistency(
            df_eng,
            delay_hours_column=delay_hours_column,
            delayed_column=delayed_column,
            display=display
        )
        if display:
            print()

        # Step 4: Create interaction features
        if display:
            print("Step 4: Creating interaction features...")
        df_eng, interaction_cols = FeatureEngineering.create_interaction_features(
            df_eng,
            display=display
        )

        # Step 5: Create ordinal features (weather severity, mode urgency, schedule risk)
        if display:
            print("\nStep 5: Creating ordinal features...")
        df_eng, ordinal_cols = FeatureEngineering.create_ordinal_features(
            df_eng,
            display=display
        )

        # Step 6: Create group-aggregate features (carrier & region level)
        if display:
            print("\nStep 6: Creating group-aggregate features...")
        df_eng, group_agg_cols = FeatureEngineering.create_group_aggregate_features(
            df_eng,
            display=display
        )

        if display:
            print("\n" + "=" * 80)
            print("FEATURE ENGINEERING PRE-SPLIT PIPELINE COMPLETED")
            print("=" * 80)
        
        return df_eng
    
    @staticmethod
    def data_split_pipeline(df: pd.DataFrame,
                           feature_columns: List[str],
                           target_class_column: str = 'delayed',
                           target_reg_column: str = 'delay_hours',
                           test_size: float = 0.2,
                           random_state: int = 42,
                           display: bool = True) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, pd.Series, pd.Series]:
        """
        Pipeline for data splitting: X/y split and train-test split.
        
        Parameters:
        df: pandas DataFrame, input dataset
        feature_columns: List[str], list of column names to use as features (X)
        target_class_column: str, name of classification target column (default: 'delayed')
        target_reg_column: str, name of regression target column (default: 'delay_hours')
        test_size: float, proportion of dataset for test split (default: 0.2)
        random_state: int, random seed for reproducibility (default: 42)
        display: bool, whether to print information (default: True)
        
        Returns:
        X_train: DataFrame, training features
        X_test: DataFrame, testing features
        y_train_class: Series, training classification target
        y_test_class: Series, testing classification target
        y_train_reg: Series, training regression target
        y_test_reg: Series, testing regression target
        """
        if display:
            print("=" * 80)
            print("DATA SPLIT PIPELINE")
            print("=" * 80)
            print()
        
        # Step 1: Create X and Y datasets
        if display:
            print("Step 1: Splitting X and Y...")
        X = df[feature_columns].copy()
        y_class = df[target_class_column].copy()
        y_reg = df[target_reg_column].copy()
        
        if display:
            print(f"  Total records: {len(df)}")
            print(f"  Number of features: {len(feature_columns)}")
            print(f"  Feature columns (X): {feature_columns}")
            print(f"  X shape: {X.shape}")
            print(f"  y_class shape: {y_class.shape} (Classification target: '{target_class_column}')")
            print(f"  y_reg shape: {y_reg.shape} (Regression target: '{target_reg_column}')")
            print()
        
        # Step 2: Train-test split for classification (with stratify)
        if display:
            print("Step 2: Performing train-test split...")
        X_train, X_test, y_train_class, y_test_class = FeatureEngineering.train_test_split_data(
            X, y_class,
            test_size=test_size,
            random_state=random_state,
            stratify=y_class,  # Stratify by y_class for balanced class distribution
            display=display
        )
        
        # Step 3: Extract regression targets using same indices
        y_train_reg = y_reg.iloc[X_train.index]
        y_test_reg = y_reg.iloc[X_test.index]
        
        if display:
            print(f"\nStep 3: Extracting regression targets...")
            print(f"  y_train_reg shape: {y_train_reg.shape} (Regression)")
            print(f"  y_test_reg shape: {y_test_reg.shape} (Regression)")
            print()
            print("=" * 80)
            print("DATA SPLIT PIPELINE COMPLETED")
            print("=" * 80)
        
        return X_train, X_test, y_train_class, y_test_class, y_train_reg, y_test_reg

    @staticmethod
    def feature_eng_encode_only_pipeline(X_train: pd.DataFrame,
                                         X_test: pd.DataFrame,
                                         categorical_columns: Optional[List[str]] = None,
                                         drop_first: bool = True,
                                         prefix_separator: str = "_",
                                         standardize_names: bool = True,
                                         display: bool = True) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
        """
        Pipeline for one-hot encoding only (no scaling) - for tree-based models.
        Tree-based models need numeric features (one-hot encoding) but don't require scaling.
        
        Parameters:
        X_train: pandas DataFrame, training features
        X_test: pandas DataFrame, testing features
        categorical_columns: Optional[List[str]], categorical columns to encode. If None, auto-detects (default: None)
        drop_first: bool, whether to drop first category in one-hot encoding (default: True)
        prefix_separator: str, separator for one-hot encoded column names (default: "_")
        standardize_names: bool, whether to standardize column names after encoding (default: True)
        display: bool, whether to print information (default: True)
        
        Returns:
        X_train_encoded: DataFrame, one-hot encoded training features (not scaled)
        X_test_encoded: DataFrame, one-hot encoded testing features (not scaled)
        encoded_columns: List[str], list of one-hot encoded column names
        """
        if display:
            print("=" * 80)
            print("FEATURE ENGINEERING: ONE-HOT ENCODING ONLY (NO SCALING)")
            print("=" * 80)
            print("Note: For tree-based models (Decision Tree, Random Forest, AdaBoost, XGBoost, LightGBM)")
            print("=" * 80)
            print()
        
        # Step 1: One-hot encode categorical features
        if display:
            print("Step 1: One-hot encoding categorical features...")
        X_train_encoded, X_test_encoded, encoded_cols = FeatureEngineering.one_hot_encode(
            X_train, X_test,
            categorical_columns=categorical_columns,
            drop_first=drop_first,
            prefix_separator=prefix_separator,
            display=display
        )
        if display:
            print()
        
        # Step 2: Standardize column names (optional)
        if standardize_names:
            if display:
                print("Step 2: Standardizing column names...")
            
            original_train_cols = X_train_encoded.columns.tolist()
            original_encoded_cols = encoded_cols.copy()
            
            X_train_encoded = DataProcessing.standardize_column_names(X_train_encoded)
            X_test_encoded = DataProcessing.standardize_column_names(X_test_encoded)
            
            # Update encoded_cols list with standardized names
            col_mapping = dict(zip(original_train_cols, X_train_encoded.columns))
            encoded_cols = [col_mapping.get(col, col) for col in original_encoded_cols if col in col_mapping]
            
            if display:
                print(f"  Standardized {len(X_train_encoded.columns)} column names")
                print()
        
        if display:
            print("=" * 80)
            print("FEATURE ENGINEERING: ONE-HOT ENCODING ONLY COMPLETED")
            print("=" * 80)
            print(f"\nFinal X_train_encoded (no scaling) shape: {X_train_encoded.shape}")
            print(f"Final X_test_encoded (no scaling) shape: {X_test_encoded.shape}")
            print(f"Number of encoded columns: {len(encoded_cols)}")
            print("=" * 80)
        
        return X_train_encoded, X_test_encoded, encoded_cols

    @staticmethod
    def feature_eng_post_split_pipeline(X_train: pd.DataFrame,
                               X_test: pd.DataFrame,
                               numeric_columns: Optional[List[str]] = None,
                               categorical_columns: Optional[List[str]] = None,
                               drop_first: bool = False,
                               prefix_separator: str = "_",
                               standardize_names: bool = True,
                               display: bool = True) -> Tuple[pd.DataFrame, pd.DataFrame, List[str], StandardScaler]:
        """
        Pipeline for feature engineering after data split: scaling and one-hot encoding.
        
        Parameters:
        X_train: pandas DataFrame, training features
        X_test: pandas DataFrame, testing features
        numeric_columns: Optional[List[str]], numeric columns to scale. If None, auto-detects (default: None)
        categorical_columns: Optional[List[str]], categorical columns to encode. If None, auto-detects (default: None)
        drop_first: bool, whether to drop first category in one-hot encoding (default: False)
        prefix_separator: str, separator for one-hot encoded column names (default: "_")
        standardize_names: bool, whether to standardize column names after encoding (default: True)
        display: bool, whether to print information (default: True)
        
        Returns:
        X_train_final: DataFrame, final processed training features
        X_test_final: DataFrame, final processed testing features
        encoded_columns: List[str], list of one-hot encoded column names
        scaler: StandardScaler, fitted scaler object
        """
        if display:
            print("=" * 80)
            print("FEATURE ENGINEERING POST-SPLIT PIPELINE")
            print("=" * 80)
            print()
        
        # Step 1: Scale numeric features
        if display:
            print("Step 1: Scaling numeric features...")
        X_train_scaled, X_test_scaled, scaler = FeatureEngineering.scale_features(
            X_train, X_test,
            numeric_columns=numeric_columns,
            display=display
        )
        if display:
            print()
        
        # Step 2: One-hot encode categorical features
        if display:
            print("Step 2: One-hot encoding categorical features...")
        X_train_encoded, X_test_encoded, encoded_cols = FeatureEngineering.one_hot_encode(
            X_train_scaled, X_test_scaled,
            categorical_columns=categorical_columns,
            drop_first=drop_first,
            prefix_separator=prefix_separator,
            display=display
        )
        if display:
            print()
        
        # Step 3: Standardize column names (optional)
        if standardize_names:
            if display:
                print("Step 3: Standardizing column names...")
            
            original_train_cols = X_train_encoded.columns.tolist()
            original_encoded_cols = encoded_cols.copy()
            
            X_train_encoded = DataProcessing.standardize_column_names(X_train_encoded)
            X_test_encoded = DataProcessing.standardize_column_names(X_test_encoded)
            
            # Update encoded_cols list with standardized names
            col_mapping = dict(zip(original_train_cols, X_train_encoded.columns))
            encoded_cols = [col_mapping.get(col, col) for col in original_encoded_cols if col in col_mapping]
            
            if display:
                print(f"  Standardized {len(X_train_encoded.columns)} column names")
                print()
        
        if display:
            print("=" * 80)
            print("FEATURE ENGINEERING POST-SPLIT PIPELINE COMPLETED")
            print("=" * 80)
            print(f"\nFinal X_train shape: {X_train_encoded.shape}")
            print(f"Final X_test shape: {X_test_encoded.shape}")
            print(f"Number of encoded columns: {len(encoded_cols)}")
            print("=" * 80)
        
        return X_train_encoded, X_test_encoded, encoded_cols, scaler


