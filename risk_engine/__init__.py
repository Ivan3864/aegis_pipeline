# risk_engine/inference.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, Optional
import time

import numpy as np
import pandas as pd

# if your model is IsolationForest
from sklearn.ensemble import IsolationForest

FEATURES = ["temp_c", "brightness_pct", "ts_epoch"]

def load_risk_model():
    # whatever you already do
    # return trained model (IsolationForest)
    ...

def compute_risk(temp_c, brightness_pct, ts_epoch, model) -> Tuple[float, str]:
    """
    Returns:
      score: float (0..1 or 0..100 depending on your design)
      label: str
    """

    # IMPORTANT: keep feature names consistent with training
    X = pd.DataFrame([{
        "temp_c": float(temp_c),
        "brightness_pct": float(brightness_pct),
        "ts_epoch": float(ts_epoch),
    }], columns=FEATURES)

    # decision_function: higher = more normal
    df = float(model.decision_function(X)[0])

    # Convert to a "risk score". You can keep your existing logic,
    # but here is a simple stable mapping:
    # map normality [-0.2..0.2] into 0..1, clamp.
    # (tune lo/hi if needed)
    lo, hi = -0.05, 0.25
    normality = (df - lo) / (hi - lo)
    normality = max(0.0, min(1.0, normality))

    # If you want risk instead of normality:
    risk = 1.0 - normality

    # label thresholds
    if risk < 0.4:
        label = "normal"
    elif risk < 0.7:
        label = "suspicious"
    else:
        label = "high"

    return risk, label
