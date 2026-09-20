"""Evaluate the Isolation Forest model against the synthetic dataset.

Deterministic evaluation script mirroring the methodology of
evaluate_baseline.py so the two experiments are directly comparable:
- reads ate_synthetic_dataset.csv (never modifies it)
- validates required columns and value formats (fails loudly, never guesses)
- builds an 80/20 stratified train/test split (random_state=42)
- fits Isolation Forest on the training features only
- evaluates on the held-out test set with the same metric formulas,
  zero-denominator policy, and labeled confusion matrix as the baseline
- writes ml_results.json and the exact test-set session ids to
  ml_test_set_session_ids.json (for the next phase's fair comparison)

Label boundary (critical):
- label is used ONLY to (a) build the stratified split and (b) evaluate
  predictions after the model has made them.
- label is NEVER passed to IsolationForest.fit(); the model fits only on
  the four feature columns.

No randomness beyond the fixed random_state=42; no sampling; no tuning.
"""

import csv
import json
import sys
from pathlib import Path

import sklearn
from sklearn.model_selection import train_test_split

from app.services.isolation_forest_scorer import (
    CONTAMINATION,
    FEATURE_COLUMNS,
    N_ESTIMATORS,
    RANDOM_STATE,
    build_model,
    feature_row,
    to_project_predictions,
)

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "ate_synthetic_dataset.csv"
RESULTS_PATH = BASE_DIR / "ml_results.json"
SESSION_IDS_PATH = BASE_DIR / "ml_test_set_session_ids.json"

REQUIRED_COLUMNS = [
    "session_id",
    "attack_type",
    "geo_velocity_kmh",
    "device_mismatch_score",
    "token_reuse_flag",
    "login_burst_count",
    "label",
]

TEST_SIZE = 0.2

# Strict token parsing: the dataset stores booleans as "True"/"False".
TOKEN_VALUES = {"True": True, "False": False}

# Frozen split expectations (verified against the dataset before approval).
# If the dataset ever changes, this script must fail loudly instead of
# silently evaluating a different experiment.
EXPECTED_TRAIN_ROWS = 1600
EXPECTED_TEST_ROWS = 400
EXPECTED_TEST_LABEL_COUNTS = {0: 320, 1: 80}
EXPECTED_TEST_ATTACK_TYPES = {
    "none": 320,
    "device_takeover": 23,
    "credential_stuffing": 22,
    "token_replay": 20,
    "impossible_travel": 15,
}


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
            session_id = raw["session_id"]
            attack_type = raw["attack_type"]
            if not session_id or not attack_type:
                raise SystemExit(
                    f"ERROR: {path.name}: line {line_no}: "
                    "empty session_id or attack_type."
                )
            token_raw = raw["token_reuse_flag"]
            if token_raw not in TOKEN_VALUES:
                raise SystemExit(
                    f"ERROR: {path.name}: line {line_no}: unexpected "
                    f"token_reuse_flag value {token_raw!r} (expected 'True'/'False')."
                )
            try:
                label = int(raw["label"])
                row = {
                    "session_id": session_id,
                    "attack_type": attack_type,
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


def evaluate(
    test_rows: list[dict],
    test_labels: list[int],
    predictions: list[int],
) -> dict:
    """Compute the metrics record for the held-out test set."""
    tp = fp = tn = fn = 0
    label_counts = {0: 0, 1: 0}
    breakdown: dict[str, dict[str, int]] = {}

    for row, label, prediction in zip(test_rows, test_labels, predictions):
        if prediction not in (0, 1):
            raise SystemExit(
                f"ERROR: prediction outside the project convention: {prediction}"
            )

        cell = breakdown.setdefault(
            row["attack_type"], {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
        )

        label_counts[label] += 1
        if prediction == 1:
            if label == 1:
                tp += 1
                cell["TP"] += 1
            else:
                fp += 1
                cell["FP"] += 1
        else:
            if label == 0:
                tn += 1
                cell["TN"] += 1
            else:
                fn += 1
                cell["FN"] += 1

    total = len(test_rows)
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

    # Deterministic ordering: "none" first (if present), then alphabetical.
    ordered_types = (["none"] if "none" in breakdown else []) + sorted(
        t for t in breakdown if t != "none"
    )
    ordered_breakdown = {t: breakdown[t] for t in ordered_types}

    return {
        "sample_count": total,
        "class_distribution": {
            "label_0": label_counts[0],
            "label_1": label_counts[1],
            "label_0_pct": label_counts[0] / total * 100,
            "label_1_pct": label_counts[1] / total * 100,
        },
        "metrics": {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "false_positive_rate": false_positive_rate,
        },
        "confusion_matrix": {"TP": tp, "FP": fp, "TN": tn, "FN": fn},
        "breakdown_by_attack_type": ordered_breakdown,
    }


def main() -> None:
    rows = load_rows(DATASET_PATH)

    # Label boundary: label is used ONLY to (a) build this stratified split
    # and (b) evaluate predictions after the model has made them. It is never
    # passed to IsolationForest.fit(); the model fits only on the four
    # feature columns.
    X = [
        feature_row(
            row["geo_velocity_kmh"],
            row["device_mismatch_score"],
            row["token_reuse_flag"],
            row["login_burst_count"],
        )
        for row in rows
    ]
    y = [row["label"] for row in rows]

    train_idx, test_idx = train_test_split(
        list(range(len(rows))),
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        stratify=y,
    )

    # Frozen-split guard: fail loudly if the dataset no longer yields the
    # verified 1600/400 partition, instead of silently evaluating a
    # different experiment.
    test_labels = [y[i] for i in test_idx]
    test_attack_counts: dict[str, int] = {}
    for i in test_idx:
        key = rows[i]["attack_type"]
        test_attack_counts[key] = test_attack_counts.get(key, 0) + 1
    observed_label_counts = {0: test_labels.count(0), 1: test_labels.count(1)}
    if (
        len(train_idx) != EXPECTED_TRAIN_ROWS
        or len(test_idx) != EXPECTED_TEST_ROWS
        or observed_label_counts != EXPECTED_TEST_LABEL_COUNTS
        or dict(sorted(test_attack_counts.items()))
        != dict(sorted(EXPECTED_TEST_ATTACK_TYPES.items()))
    ):
        raise SystemExit(
            "ERROR: train/test split no longer matches the verified partition; "
            "the dataset likely changed. Stopping."
        )

    # Label boundary: fit receives ONLY the four feature columns; labels are
    # not visible to the model.
    model = build_model()
    model.fit([X[i] for i in train_idx])

    predictions = to_project_predictions(model.predict([X[i] for i in test_idx]))

    test_rows = [rows[i] for i in test_idx]
    evaluation = evaluate(test_rows, test_labels, predictions)

    results = {
        "model": "isolation_forest",
        "dataset": DATASET_PATH.name,
        "sample_count": evaluation["sample_count"],
        "dataset_size": len(rows),
        "class_distribution": evaluation["class_distribution"],
        "features": FEATURE_COLUMNS,
        "feature_encoding": {
            "token_reuse_flag": "boolean True -> 1, False -> 0",
        },
        "model_parameters": {
            "contamination": CONTAMINATION,
            "n_estimators": N_ESTIMATORS,
            "random_state": RANDOM_STATE,
        },
        "anomaly_label_conversion": {
            "sklearn -1": 1,
            "sklearn 1": 0,
            "note": (
                "scikit-learn returns -1 for anomalies and 1 for normal "
                "points; mapped to the project convention 1 = "
                "flagged/suspicious, 0 = not flagged."
            ),
        },
        "split": {
            "method": "train_test_split",
            "test_size": TEST_SIZE,
            "stratify": "label",
            "random_state": RANDOM_STATE,
            "train_rows": len(train_idx),
            "test_rows": len(test_idx),
            "test_session_ids_file": SESSION_IDS_PATH.name,
        },
        "prediction_semantics": (
            "Prediction 1 = flagged/suspicious (sklearn anomaly, output -1); "
            "0 = not flagged (sklearn normal, output 1)."
        ),
        "metrics": evaluation["metrics"],
        "confusion_matrix": evaluation["confusion_matrix"],
        "breakdown_by_attack_type": evaluation["breakdown_by_attack_type"],
        "library_versions": {
            "scikit-learn": sklearn.__version__,
            "python": (
                f"{sys.version_info.major}.{sys.version_info.minor}"
                f".{sys.version_info.micro}"
            ),
        },
        "design_notes": (
            "Unsupervised fit on training features only (labels never passed "
            "to fit); contamination=0.20 fixed before evaluation, reflecting "
            "the known 20% composition of the controlled synthetic dataset "
            "(not discovered by the model, not tuned); frozen experimental "
            "parameters."
        ),
    }

    dist = evaluation["class_distribution"]
    metrics = evaluation["metrics"]
    confusion = evaluation["confusion_matrix"]
    train_label_1 = sum(y[i] for i in train_idx)
    train_label_0 = len(train_idx) - train_label_1

    print(f"Dataset: {results['dataset']} | rows: {results['dataset_size']}")
    print(
        f"Split: train {len(train_idx)} "
        f"(label0={train_label_0}, label1={train_label_1}) | "
        f"test {len(test_idx)} "
        f"(label0={dist['label_0']}, label1={dist['label_1']})"
    )
    print(
        f"Model: IsolationForest(contamination={CONTAMINATION}, "
        f"n_estimators={N_ESTIMATORS}, random_state={RANDOM_STATE})"
    )
    print("Evaluation set: held-out test set (model never fitted on these rows)")
    print("Class distribution (test set, true labels):")
    print(f"  label=0: {dist['label_0']} ({dist['label_0_pct']:.2f}%)")
    print(f"  label=1: {dist['label_1']} ({dist['label_1_pct']:.2f}%)")
    print("Metrics (test set):")
    print(f"  precision:            {metrics['precision']:.4f}")
    print(f"  recall:               {metrics['recall']:.4f}")
    print(f"  f1:                   {metrics['f1']:.4f}")
    print(f"  false_positive_rate:  {metrics['false_positive_rate']:.4f}")
    print("Confusion matrix (labeled, test set):")
    print("                        Predicted 0   Predicted 1")
    print(
        f"  Actual 0 (n={dist['label_0']:>4})   "
        f"TN={confusion['TN']:>5}      FP={confusion['FP']:>5}"
    )
    print(
        f"  Actual 1 (n={dist['label_1']:>4})   "
        f"FN={confusion['FN']:>5}      TP={confusion['TP']:>5}"
    )
    print("Breakdown by attack type (test set):")
    print("  attack_type             n     TP     FN     FP     TN")
    for attack_type, cell in evaluation["breakdown_by_attack_type"].items():
        n = cell["TP"] + cell["FP"] + cell["TN"] + cell["FN"]
        print(
            f"  {attack_type:<20} {n:>4}  {cell['TP']:>5}  {cell['FN']:>5}  "
            f"{cell['FP']:>5}  {cell['TN']:>5}"
        )

    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    session_id_payload = {
        "dataset": DATASET_PATH.name,
        "split_method": "train_test_split",
        "test_size": TEST_SIZE,
        "stratify": "label",
        "random_state": RANDOM_STATE,
        "test_rows": len(test_idx),
        "session_ids": [rows[i]["session_id"] for i in test_idx],
    }
    SESSION_IDS_PATH.write_text(
        json.dumps(session_id_payload, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Results written to: {RESULTS_PATH}")
    print(f"Test-set session ids written to: {SESSION_IDS_PATH}")


if __name__ == "__main__":
    main()
