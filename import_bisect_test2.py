"""
Throwaway diagnostic #2: isolates every import/call inside data_eda_2.py's top-level
code, one at a time, in the exact order that file executes them. Run from repo root:

    .venv/bin/python3 import_bisect_test2.py

Delete once the investigation is done.
"""
print("start", flush=True)
import pandas as pd
print("pandas ok", flush=True)
import numpy as np
print("numpy ok", flush=True)
import matplotlib
print("matplotlib (bare) ok", flush=True)
import matplotlib.pyplot as plt
print("matplotlib.pyplot ok", flush=True)
import seaborn as sns
print("seaborn ok", flush=True)
from scipy import stats
print("scipy.stats ok", flush=True)
from IPython.display import display as ipython_display
print("IPython.display ok", flush=True)
sns.set_style("whitegrid")
print("sns.set_style ok", flush=True)
print("ALL DONE", flush=True)
