"""
Throwaway diagnostic #4: dumps a stack trace of exactly where the REAL import path
prediction_server.py uses (src/__init__.py -> daily_predict.py) is frozen now, after
removing the seaborn/EDA imports from src/__init__.py. Run from repo root:

    .venv/bin/python3 import_bisect_test4.py

Wait the full 20 seconds -- faulthandler will print the live stack trace and exit on
its own. Delete this file once done.
"""
import faulthandler
import sys

faulthandler.dump_traceback_later(20, exit=True, file=sys.stderr)

sys.path.insert(0, "prediction_pipeline")

print("start", flush=True)
import src
print("src (updated __init__.py) ok", flush=True)
from src.daily_predict import DailyPredictionPipeline
print("daily_predict ok -- dump_traceback_later did not fire", flush=True)
