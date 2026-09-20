"""
ATE — Synthetic Session Dataset Generator
==========================================

Generates a labeled dataset of fintech login/session events for training
and evaluating the ATE risk-scoring engine (rule-based baseline + Isolation
Forest model).

Design (locked-in, see conversation for rationale):
- 2,000 total rows
- 80% normal sessions / 20% attack sessions
- Attack types evenly split across 4 categories (5% each of total):
    1. impossible_travel   — geo-velocity physically implausible
    2. device_takeover     — device fingerprint suddenly mismatches
    3. token_replay        — refresh token reused
    4. credential_stuffing — rapid burst of login attempts

Output: ate_synthetic_dataset.csv

Reproducible via a fixed random seed — re-running this script produces the
exact same dataset, which matters for defending your results.
"""

import random
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
SEED = 42
random.seed(SEED)
np.random.seed(SEED)

# ---------------------------------------------------------------------------
# Dataset parameters (locked-in design)
# ---------------------------------------------------------------------------
TOTAL_ROWS = 2000
NORMAL_RATIO = 0.80
ATTACK_TYPES = ["impossible_travel", "device_takeover", "token_replay", "credential_stuffing"]

N_NORMAL = int(TOTAL_ROWS * NORMAL_RATIO)          # 1600
N_ATTACK_TOTAL = TOTAL_ROWS - N_NORMAL             # 400
N_PER_ATTACK_TYPE = N_ATTACK_TOTAL // len(ATTACK_TYPES)  # 100 each

# ---------------------------------------------------------------------------
# Feature ranges (exact, defensible numbers — see design doc)
# ---------------------------------------------------------------------------
# geo_velocity_kmh: speed implied by distance/time between two consecutive logins
NORMAL_GEO_VELOCITY = (0, 900)            # walking to commercial flight speed
IMPOSSIBLE_TRAVEL_GEO_VELOCITY = (1000, 10000)  # physically implausible

# device_mismatch_score: 0 = identical fingerprint, 1 = completely different device
NORMAL_DEVICE_MISMATCH = (0.0, 0.1)       # minor natural variation (browser update etc.)
DEVICE_TAKEOVER_MISMATCH = (0.7, 1.0)     # clearly a different device

# login_burst_count: number of login attempts in the preceding 60-second window
NORMAL_BURST_COUNT = (1, 2)
CREDENTIAL_STUFFING_BURST = (10, 30)

# time_since_last_login_minutes: gap since the user's previous login
NORMAL_TIME_GAP = (5, 1440)               # 5 minutes to 24 hours — normal usage pattern
ATTACK_TIME_GAP = (0, 5)                  # attacks often happen in quick succession


def random_timestamp(days_back: int = 30) -> datetime:
    """Random timestamp within the last `days_back` days."""
    now = datetime.now(timezone.utc)
    delta_seconds = random.randint(0, days_back * 24 * 60 * 60)
    return now - timedelta(seconds=delta_seconds)


def make_row(attack_type: str | None) -> dict:
    """
    Build one session-event row.
    attack_type = None means a normal session.
    """
    is_attack = attack_type is not None

    row = {
        "session_id": str(uuid.uuid4()),
        "user_id": f"user_{random.randint(1000, 1999)}",
        "timestamp": random_timestamp().isoformat(),
        "attack_type": attack_type if is_attack else "none",
        "label": 1 if is_attack else 0,
    }

    # --- geo_velocity_kmh ---
    if attack_type == "impossible_travel":
        row["geo_velocity_kmh"] = round(random.uniform(*IMPOSSIBLE_TRAVEL_GEO_VELOCITY), 2)
    else:
        row["geo_velocity_kmh"] = round(random.uniform(*NORMAL_GEO_VELOCITY), 2)

    # --- device_mismatch_score ---
    if attack_type == "device_takeover":
        row["device_mismatch_score"] = round(random.uniform(*DEVICE_TAKEOVER_MISMATCH), 3)
    else:
        row["device_mismatch_score"] = round(random.uniform(*NORMAL_DEVICE_MISMATCH), 3)

    # --- token_reuse_flag ---
    row["token_reuse_flag"] = attack_type == "token_replay"

    # --- login_burst_count ---
    if attack_type == "credential_stuffing":
        row["login_burst_count"] = random.randint(*CREDENTIAL_STUFFING_BURST)
    else:
        row["login_burst_count"] = random.randint(*NORMAL_BURST_COUNT)

    # --- time_since_last_login_minutes ---
    if is_attack:
        row["time_since_last_login_minutes"] = round(random.uniform(*ATTACK_TIME_GAP), 2)
    else:
        row["time_since_last_login_minutes"] = round(random.uniform(*NORMAL_TIME_GAP), 2)

    return row


def generate_dataset() -> pd.DataFrame:
    rows = []

    # Normal sessions
    for _ in range(N_NORMAL):
        rows.append(make_row(attack_type=None))

    # Attack sessions — evenly split across the 4 types
    for attack_type in ATTACK_TYPES:
        for _ in range(N_PER_ATTACK_TYPE):
            rows.append(make_row(attack_type=attack_type))

    df = pd.DataFrame(rows)
    # Shuffle so attack rows aren't grouped together at the end of the file
    df = df.sample(frac=1, random_state=SEED).reset_index(drop=True)
    return df


def print_summary(df: pd.DataFrame) -> None:
    print(f"Total rows: {len(df)}")
    print(f"Normal: {(df['label'] == 0).sum()}  ({(df['label'] == 0).mean():.1%})")
    print(f"Attack: {(df['label'] == 1).sum()}  ({(df['label'] == 1).mean():.1%})")
    print("\nAttack type breakdown:")
    print(df[df["label"] == 1]["attack_type"].value_counts())
    print("\nSample rows:")
    print(df.head(5).to_string(index=False))


if __name__ == "__main__":
    dataset = generate_dataset()
    output_path = "ate_synthetic_dataset.csv"
    dataset.to_csv(output_path, index=False)
    print(f"Dataset written to {output_path}\n")
    print_summary(dataset)