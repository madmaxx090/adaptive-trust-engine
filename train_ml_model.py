"""Train and persist the live Isolation Forest artifact (frozen Phase 5 parameters).

Reproduces the exact Phase 5 experiment:
- reads ate_synthetic_dataset.csv via evaluate_ml.load_rows (never modifies it)
- rebuilds the same 80/20 stratified split (train_test_split, random_state=42)
  and fails loudly if the verified 1600/400 partition no longer holds
- fits build_model() (contamination=0.20, n_estimators=100, random_state=42)
  on the training split only (the label is never passed to fit)
- saves the fitted model to ml_model/isolation_forest_v1.joblib (joblib)
- verifies the SAVED artifact by reloading it and re-evaluating the held-out
  test split, comparing the result against the frozen ml_results.json
  (metrics, confusion matrix, attack-type breakdown). Any mismatch stops the
  script with a non-zero exit: the artifact must reproduce the validated
  offline evaluation before it may be wired into the live API.
- writes ml_model/isolation_forest_v1_verification.json: artifact SHA-256,
  library versions, split info, and deterministic golden inference samples
  used by the in-container cross-environment equivalence test.

Run on the host with the validated Phase 5 environment
(Python 3.13, scikit-learn 1.9.0, joblib 1.6.0):

    python train_ml_model.py

This script imports the frozen Phase 5 modules read-only; it modifies nothing.
"""

import hashlib
import json
import sys
from pathlib import Path

import joblib
import sklearn
from sklearn.model_selection import train_test_split

from app.services.isolation_forest_scorer import (
    RANDOM_STATE,
    build_model,
    feature_row,
    to_project_predictions,
)
from evaluate_ml import (
    DATASET_PATH,
    EXPECTED_TEST_ATTACK_TYPES,
    EXPECTED_TEST_LABEL_COUNTS,
    EXPECTED_TEST_ROWS,
    EXPECTED_TRAIN_ROWS,
    RESULTS_PATH,
    TEST_SIZE,
    evaluate,
    load_rows,
)

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "ml_model"
MODEL_PATH = MODEL_DIR / "isolation_forest_v1.joblib"
VERIFICATION_PATH = MODEL_DIR / "isolation_forest_v1_verification.json"

# The validated Phase 5 evaluation environment. The artifact must be produced
# with these exact versions; otherwise deterministic reproduction of the
# frozen evaluation cannot be guaranteed and the script stops instead of
# producing an unverifiable model (no silent version drift).
EXPECTED_SKLEARN_VERSION = "1.9.0"
EXPECTED_JOBLIB_VERSION = "1.6.0"

# Deterministic golden samples for the in-container cross-environment check.
GOLDEN_SAMPLE_COUNT = 25


def main() -> None:
    if sklearn.__version__ != EXPECTED_SKLEARN_VERSION:
        raise SystemExit(
            f"ERROR: scikit-learn {sklearn.__version__} != validated "
            f"{EXPECTED_SKLEARN_VERSION}; refusing to train outside the "
            "validated Phase 5 environment."
        )
    if joblib.__version__ != EXPECTED_JOBLIB_VERSION:
        raise SystemExit(
            f"ERROR: joblib {joblib.__version__} != validated "
            f"{EXPECTED_JOBLIB_VERSION}; refusing to train outside the "
            "validated Phase 5 environment."
        )

    rows = load_rows(DATASET_PATH)

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

    # Frozen-split guard (same check as evaluate_ml.main): fail loudly if the
    # dataset no longer yields the verified 1600/400 partition.
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

    # Fit on the training split only; the label is never passed to fit().
    model = build_model()
    model.fit([X[i] for i in train_idx])

    MODEL_DIR.mkdir(exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    print(f"Saved trained model artifact to: {MODEL_PATH}")

    # Verification gate: the SAVED artifact (not the in-memory model) must
    # reproduce the frozen offline evaluation exactly.
    loaded = joblib.load(MODEL_PATH)
    predictions = to_project_predictions(loaded.predict([X[i] for i in test_idx]))
    test_rows = [rows[i] for i in test_idx]
    evaluation = evaluate(test_rows, test_labels, predictions)

    frozen = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    checks = {
        "sample_count": evaluation["sample_count"] == frozen["sample_count"],
        "class_distribution": (
            evaluation["class_distribution"] == frozen["class_distribution"]
        ),
        "metrics": evaluation["metrics"] == frozen["metrics"],
        "confusion_matrix": (
            evaluation["confusion_matrix"] == frozen["confusion_matrix"]
        ),
        "breakdown_by_attack_type": (
            evaluation["breakdown_by_attack_type"]
            == frozen["breakdown_by_attack_type"]
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise SystemExit(
            f"ERROR: the saved artifact does not reproduce {RESULTS_PATH.name} "
            f"(mismatched sections: {failed}). Stopping."
        )
    print(
        f"Gate OK: the saved artifact reproduces {RESULTS_PATH.name} exactly "
        "(metrics, confusion matrix, attack-type breakdown, class distribution)."
    )

    # Deterministic golden inference samples for the in-container check.
    golden_rows = test_idx[:GOLDEN_SAMPLE_COUNT]
    raw_predictions = loaded.predict([X[i] for i in golden_rows])
    decision_scores = loaded.decision_function([X[i] for i in golden_rows])
    golden_samples = [
        {
            "features": [float(value) for value in X[i]],
            "predict": int(raw),
            "flag": int(flag),
            "decision_score": float(score),
        }
        for i, raw, flag, score in zip(
            golden_rows,
            raw_predictions,
            to_project_predictions(raw_predictions),
            decision_scores,
        )
    ]

    artifact_sha256 = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest().upper()
    verification = {
        "model_file": MODEL_PATH.name,
        "model_sha256": artifact_sha256,
        "scikit_learn_version": sklearn.__version__,
        "joblib_version": joblib.__version__,
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}"
            f".{sys.version_info.micro}"
        ),
        "dataset": DATASET_PATH.name,
        "split": {
            "method": "train_test_split",
            "test_size": TEST_SIZE,
            "stratify": "label",
            "random_state": RANDOM_STATE,
            "train_rows": len(train_idx),
            "test_rows": len(test_idx),
        },
        "golden_sample_count": len(golden_samples),
        "golden_samples": golden_samples,
    }
    VERIFICATION_PATH.write_text(
        json.dumps(verification, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Verification record written to: {VERIFICATION_PATH}")
    print(f"Model artifact SHA-256: {artifact_sha256}")
    print(f"Golden samples: {len(golden_samples)} (held-out test split)")


if __name__ == "__main__":
    main()
