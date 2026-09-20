"""Evaluate the fixed rule-based baseline against the synthetic dataset.

Deterministic, standard-library-only evaluation script:
- reads ate_synthetic_dataset.csv (never modifies it)
- validates required columns and value formats (fails loudly, never guesses)
- scores every row with app.services.baseline_scorer
- computes precision, recall, F1, false-positive rate, a labeled confusion
  matrix, and the class distribution
- writes the permanent experiment record to baseline_results.json

No randomness, no sampling, no tuning: identical input -> identical output.
"""

import csv
import json
from pathlib import Path

from app.services.baseline_scorer import (
    PREDICTION_BY_TIER,
    TIER_LOW_MAX,
    TIER_MEDIUM_MAX,
    WEIGHTS,
    score_session,
)

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "ate_synthetic_dataset.csv"
RESULTS_PATH = BASE_DIR / "baseline_results.json"

REQUIRED_COLUMNS = [
    "geo_velocity_kmh",
    "device_mismatch_score",
    "token_reuse_flag",
    "login_burst_count",
    "label",
]

# Strict token parsing: the dataset stores booleans as "True"/"False".
TOKEN_VALUES = {"True": True, "False": False}


def load_rows(path: Path) -> list[dict]:
    """Read and validate the dataset rows; exit with an error on any mismatch."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise SystemExit(f"ERROR: {path.name}: no header row found.")
        missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise SystemExit(
                f"ERROR: {path.name}: missing required column(s): {missing}"
            )

        rows = []
        for line_no, raw in enumerate(reader, start=2):
            token_raw = raw["token_reuse_flag"]
            if token_raw not in TOKEN_VALUES:
                raise SystemExit(
                    f"ERROR: {path.name}: line {line_no}: unexpected "
                    f"token_reuse_flag value {token_raw!r} (expected 'True'/'False')."
                )
            try:
                label = int(raw["label"])
                row = {
                    "geo_velocity_kmh": float(raw["geo_velocity_kmh"]),
                    "device_mismatch_score": float(raw["device_mismatch_score"]),
                    "login_burst_count": int(raw["login_burst_count"]),
                    "token_reuse_flag": TOKEN_VALUES[token_raw],
                    "label": label,
                }
            except ValueError as exc:
                raise SystemExit(
                    f"ERROR: {path.name}: line {line_no}: "
                    f"unparseable numeric value ({exc})."
                ) from exc
            if label not in (0, 1):
                raise SystemExit(
                    f"ERROR: {path.name}: line {line_no}: label must be 0 or 1."
                )
            rows.append(row)
        return rows


def evaluate(rows: list[dict]) -> dict:
    """Score all rows and build the results record."""
    tp = fp = tn = fn = 0
    label_counts = {0: 0, 1: 0}
    tier_counts = {"low": 0, "medium": 0, "high": 0}

    for row in rows:
        score, tier, prediction = score_session(
            row["geo_velocity_kmh"],
            row["device_mismatch_score"],
            row["token_reuse_flag"],
            row["login_burst_count"],
        )
        if not 0.0 <= score <= 100.0:
            raise SystemExit(f"ERROR: risk score out of the 0-100 range: {score}")

        label = row["label"]
        label_counts[label] += 1
        tier_counts[tier] += 1
        if prediction == 1:
            if label == 1:
                tp += 1
            else:
                fp += 1
        else:
            if label == 0:
                tn += 1
            else:
                fn += 1

    total = len(rows)
    if tp + fp + tn + fn != total:
        raise SystemExit(
            "ERROR: confusion-matrix counts do not reconcile with row count."
        )
    if tp + fn != label_counts[1] or fp + tn != label_counts[0]:
        raise SystemExit("ERROR: predictions do not reconcile with the true labels.")

    # Zero-denominator policy: undefined ratios are reported as 0.0 (documented).
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    false_positive_rate = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    return {
        "model": "rule_based_baseline",
        "dataset": DATASET_PATH.name,
        "sample_count": total,
        "class_distribution": {
            "label_0": label_counts[0],
            "label_1": label_counts[1],
            "label_0_pct": label_counts[0] / total * 100,
            "label_1_pct": label_counts[1] / total * 100,
        },
        "weights": WEIGHTS,
        "normalization": {
            "geo_velocity": "min(geo_velocity_kmh / 1000, 1) * 100",
            "device_mismatch": "device_mismatch_score * 100",
            "token_reuse": "100 if token_reuse_flag else 0",
            "login_burst": "min(login_burst_count / 30, 1) * 100",
        },
        "risk_tiers": {"low": "0-40", "medium": "41-70", "high": "71-100"},
        "tier_boundary_policy": (
            f"applied to unrounded scores: score <= {TIER_LOW_MAX} -> low; "
            f"{TIER_LOW_MAX} < score <= {TIER_MEDIUM_MAX} -> medium; "
            f"score > {TIER_MEDIUM_MAX} -> high"
        ),
        "prediction_mapping": PREDICTION_BY_TIER,
        "prediction_semantics": (
            "Medium and High are treated as flagged/suspicious sessions "
            "(prediction = 1); Low is treated as non-flagged (prediction = 0)."
        ),
        "metrics": {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "false_positive_rate": false_positive_rate,
        },
        "confusion_matrix": {"TP": tp, "FP": fp, "TN": tn, "FN": fn},
        "tier_distribution": tier_counts,
        "design_notes": (
            "Fixed heuristic weights (not learned, fitted, or tuned on this "
            "dataset); fixed normalization caps; deterministic static reference "
            "baseline for later ML/adaptive comparison."
        ),
    }


def main() -> None:
    rows = load_rows(DATASET_PATH)
    results = evaluate(rows)

    dist = results["class_distribution"]
    tiers = results["tier_distribution"]
    metrics = results["metrics"]
    confusion = results["confusion_matrix"]

    print(f"Dataset: {results['dataset']} | rows: {results['sample_count']}")
    print("Class distribution (true labels):")
    print(f"  label=0: {dist['label_0']} ({dist['label_0_pct']:.2f}%)")
    print(f"  label=1: {dist['label_1']} ({dist['label_1_pct']:.2f}%)")
    print("Tier distribution (predicted):")
    print(
        f"  low: {tiers['low']} | medium: {tiers['medium']} | high: {tiers['high']}"
    )
    print("Metrics:")
    print(f"  precision:            {metrics['precision']:.4f}")
    print(f"  recall:               {metrics['recall']:.4f}")
    print(f"  f1:                   {metrics['f1']:.4f}")
    print(f"  false_positive_rate:  {metrics['false_positive_rate']:.4f}")
    print("Confusion matrix (labeled):")
    print("                        Predicted 0   Predicted 1")
    print(
        f"  Actual 0 (n={dist['label_0']:>4})   "
        f"TN={confusion['TN']:>5}      FP={confusion['FP']:>5}"
    )
    print(
        f"  Actual 1 (n={dist['label_1']:>4})   "
        f"FN={confusion['FN']:>5}      TP={confusion['TP']:>5}"
    )

    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Results written to: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
