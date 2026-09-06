"""
Source package for delay prediction project.
Exports all pipeline classes for use in the notebook.
"""

from .data_extract_1 import DataExtract
from .data_processing_3 import DataProcessing
from .feature_engineering_4 import FeatureEngineering
from .model_persistence_9 import ModelPersistence
from .database_operations_10 import DatabaseOperations

# Notebook/training-only modules -- commented out 5-Sep-26. Importing `src` (which
# prediction_server.py does via `from src.daily_predict import ...`, and every
# `from src.<module> import X` triggers regardless of which submodule is named,
# since Python must run this __init__.py before it can reach any submodule)
# previously pulled ALL ten modules in unconditionally. daily_predict.py's actual
# inference path only ever uses the five imports left active above.
#
# DataEDA pulls in seaborn -> scipy.stats -> scipy.interpolate, and scipy.interpolate's
# compiled extension (_bsplines) was observed hanging indefinitely on native-library
# load (stuck in importlib's create_module, i.e. the OS loading the .so file itself --
# not a Python-level bug), most likely a Gatekeeper/notarization check stalling after
# a macOS update reset its verification cache. That stall blocked the MCP server from
# ever completing its stdio handshake, since it never got past this import to reach
# mcp.run(). Re-enable these once the underlying scipy/seaborn native-load hang is
# resolved, if a notebook or training script needs them via `from src import X`
# (they can import the specific submodule directly in the meantime).
# from .data_eda_2 import DataEDA
# from .model_evaluation_5 import ModelEvaluation
# from .baseline_models_6 import BaselineModels
# from .regression_models_7 import RegressionModels
# from .classification_models_8 import ClassificationModels

__all__ = [
    'DataExtract',
    'DataProcessing',
    'FeatureEngineering',
    'ModelPersistence',
    'DatabaseOperations',
    # 'DataEDA', 'ModelEvaluation', 'BaselineModels', 'RegressionModels',
    # 'ClassificationModels',  # see commented-out imports above
]
