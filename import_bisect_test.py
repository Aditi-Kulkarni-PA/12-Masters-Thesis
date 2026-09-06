"""
Throwaway diagnostic: imports each prediction_pipeline/src module ONE AT A TIME,
printing after each completes, to find exactly which one hangs.

Earlier version of this script used `from src.data_extract_1 import DataExtract`,
which is wrong: since `src` is a package, that statement forces Python to fully
execute src/__init__.py (all ten imports) before returning -- so it could never
have isolated anything, no matter which import line was written first.

This version registers a stub `src` package in sys.modules (with the right
__path__) WITHOUT running the real src/__init__.py, then imports each submodule
individually via importlib so relative imports inside them (e.g. baseline_models_6.py
does `from .model_evaluation_5 import ModelEvaluation`) still resolve correctly.

Run from the repo root:
    .venv/bin/python3 import_bisect_test.py

Delete once the investigation is done.
"""
import importlib
import sys
import types

sys.path.insert(0, "prediction_pipeline")

_stub = types.ModuleType("src")
_stub.__path__ = ["prediction_pipeline/src"]
sys.modules["src"] = _stub

print("start", flush=True)
importlib.import_module("src.data_extract_1")
print("data_extract_1 ok", flush=True)
importlib.import_module("src.data_eda_2")
print("data_eda_2 ok", flush=True)
importlib.import_module("src.data_processing_3")
print("data_processing_3 ok", flush=True)
importlib.import_module("src.feature_engineering_4")
print("feature_engineering_4 ok", flush=True)
importlib.import_module("src.model_evaluation_5")
print("model_evaluation_5 ok", flush=True)
importlib.import_module("src.baseline_models_6")
print("baseline_models_6 ok", flush=True)
importlib.import_module("src.regression_models_7")
print("regression_models_7 ok", flush=True)
importlib.import_module("src.classification_models_8")
print("classification_models_8 ok", flush=True)
importlib.import_module("src.model_persistence_9")
print("model_persistence_9 ok", flush=True)
importlib.import_module("src.database_operations_10")
print("database_operations_10 ok", flush=True)
print("ALL SRC MODULES COMPLETE", flush=True)
