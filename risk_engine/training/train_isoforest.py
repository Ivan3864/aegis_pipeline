# risk_engine/training/train_isoforest.py

import os
import pandas as pd
from sklearn.ensemble import IsolationForest
from joblib import dump

# Resolve paths relative to the project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_PATH = os.path.join(BASE_DIR, "model_training", "data", "telemetry.csv")
MODEL_DIR = os.path.join(BASE_DIR, "models")
MODEL_PATH = os.path.join(MODEL_DIR, "risk_isoforest.joblib")


def load_data():
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"Dataset not found: {DATA_PATH}")

    df = pd.read_csv(DATA_PATH)

    # Keep only the numeric sensor columns we care about
    df = df[["temp_c", "brightness_pct"]].dropna()

    return df


def train():
    df = load_data()
    print(f"Loaded {len(df)} rows from {DATA_PATH}")

    model = IsolationForest(
        n_estimators=200,
        contamination=0.05,   # assume about 5 percent anomalies
        random_state=42
    )

    model.fit(df)
    os.makedirs(MODEL_DIR, exist_ok=True)
    dump(model, MODEL_PATH)

    print(f"Risk model trained and saved to {MODEL_PATH}")


if __name__ == "__main__":
    train()
