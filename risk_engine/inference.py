# risk_engine/inference.py

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import joblib
import numpy as np

# Base directory of the project (aegis_pipeline/)
BASE_DIR = Path(__file__).resolve().parents[1]
MODEL_PATH = BASE_DIR / "models" / "risk_isoforest.joblib"


def load_risk_model():
    """
    Load the IsolationForest model trained on temperature and brightness.
    Returns the model instance or None if it cannot be loaded.
    """
    try:
        model = joblib.load(MODEL_PATH)
        print(f"[AI] risk model loaded from {MODEL_PATH}")
        return model
    except Exception as e:
        print(f"[AI] could not load risk model: {e}")
        return None


def compute_risk(
    temp_c: float,
    brightness_pct: float,
    ts_epoch: Optional[float] = None,
    model=None,
) -> Tuple[Optional[float], str]:
    """
    Compute an AI risk score for a single telemetry point.

    Features used: [temp_c, brightness_pct]
    - model.decision_function -> we invert and rescale to 0-100
    - model.predict -> used to label 'idle' vs 'elevated' / 'high'
    """
    if model is None:
        # no model loaded, keep system in idle
        return None, "idle"

    try:
        # Model was trained on exactly TWO features:
        # temperature and brightness. Do NOT include timestamp.
        X = np.array([[float(temp_c), float(brightness_pct)]], dtype=float)

        # decision_function: higher is more normal, lower is more anomalous
        df = float(model.decision_function(X)[0])
        pred = int(model.predict(X)[0])  # 1 = normal, -1 = anomaly

        # Invert and squash into [0, 100] for a human-friendly score.
        # You can tweak this formula to make scores more or less spiky.
        raw = -df  # anomalies -> larger positive numbers
        # simple normalization heuristic
        score = max(0.0, min(100.0, (raw + 0.5) * 40.0))

        # Map to labels
        if pred == -1:
            # model thinks this point is an outlier
            if score >= 70:
                label = "high"
            else:
                label = "elevated"
        else:
            # inlier, but we can still show “elevated” if score is mid-range
            if score >= 50:
                label = "elevated"
            else:
                label = "idle"

        return float(round(score, 1)), label

    except Exception as e:
        print("[AI] error computing risk:", e)
        return None, "idle"
