"""Fair comparison: frozen rule-based baseline vs Isolation Forest.

Both models are evaluated on the identical Phase 5 held-out test set (the
exact 400 session ids listed in ml_test_set_session_ids.json).

Reads (all strictly read-only):
- ate_synthetic_dataset.csv
- ml_test_set_session_ids.json (never regenerated, reshuffled, or resplit)
- ml_results.json (the Isolation Forest is NOT re-run)

Reuses, imported as-is (not rewritten, not reimplemented):
- app.services.baseline_scorer.score_session (frozen scoring function)
- evaluate_baseline.evaluate (frozen metric formulas / zero-denominator policy)

Writes (new files only, all existing artifacts stay untouched):
- baseline_on_test_set_results.json
- comparison_report.json
- comparison_report.md

Audit properties:
- Frozen files are hashed and verified against previously recorded values
  (Phase 3 / Phase 5 reports) BEFORE anything is written.
- Any mismatch (hash, missing/duplicate ids, ml_results inconsistency) stops
  the script before writing anything.
- This script does not import scikit-learn or the Isolation Forest module, so
  the model cannot be re-run here; its results are read from ml_results.json.
"""

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

# Imported as-is; do not reimplement the scoring or the metric formulas.
from app.services.baseline_scorer import score_session
from evaluate_baseline import evaluate as evaluate_frozen_baseline

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "ate_synthetic_dataset.csv"
SESSION_IDS_PATH = BASE_DIR / "ml_test_set_session_ids.json"
ML_RESULTS_PATH = BASE_DIR / "ml_results.json"
BASELINE_ON_TEST_PATH = BASE_DIR / "baseline_on_test_set_results.json"
COMPARISON_JSON_PATH = BASE_DIR / "comparison_report.json"
COMPARISON_MD_PATH = BASE_DIR / "comparison_report.md"

EXPECTED_TEST_ROWS = 400
EXPECTED_TEST_LABEL_COUNTS = {0: 320, 1: 80}

REQUIRED_COLUMNS = [
    "session_id",
    "attack_type",
    "geo_velocity_kmh",
    "device_mismatch_score",
    "token_reuse_flag",
    "login_burst_count",
    "label",
]

# Strict token parsing: the dataset stores booleans as "True"/"False".
TOKEN_VALUES = {"True": True, "False": False}

# Frozen files and their previously recorded SHA-256 values (Phase 3 / Phase 5
# reports of this project session). Files with no prior recorded value are
# listed as None: their current hash is recorded for future verification
# (first recorded at Phase 6 inspection) and is never compared against an
# invented value.
FROZEN_FILE_SPECS = [
    {
        "file": "app/services/baseline_scorer.py",
        "expected": "7FA30FDD3E04A01CF03B8A5A44ED6BB76BEEC50807F77CBE9D3A4BB97608F4DD",
        "recorded_source": "Phase 5 report",
    },
    {
        "file": "evaluate_baseline.py",
        "expected": "20C893ABCD42A1E7605AA42E6C0B0984781347DFE659EF262AE1C5CB37A451ED",
        "recorded_source": "Phase 5 report",
    },
    {
        "file": "baseline_results.json",
        "expected": "EAA77AE850DDAD7B4B24A33D0DF04FDE6216A4C224F68EFBDD1D83390210EACE",
        "recorded_source": "Phase 3 report (re-verified in Phase 5)",
    },
    {
        "file": "app/services/isolation_forest_scorer.py",
        "expected": None,
        "recorded_source": (
            "no prior value recorded; first recorded at Phase 6 inspection"
        ),
    },
    {
        "file": "evaluate_ml.py",
        "expected": None,
        "recorded_source": (
            "no prior value recorded; first recorded at Phase 6 inspection"
        ),
    },
    {
        "file": "ml_results.json",
        "expected": "3EFD4A691C001C781223A5DF7AE1ACE753BE2FD6295E270A9A3B9638311E33E9",
        "recorded_source": "Phase 5 report",
    },
    {
        "file": "ml_test_set_session_ids.json",
        "expected": "EB1FCA056B9D421C204C90BAA71989FEF7629909D5632A2222C5940CCAC8299B",
        "recorded_source": "Phase 5 report",
    },
]


def sha256_hexdigest(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    """SHA-256 hex digest of a file's exact bytes."""
    return sha256_hexdigest(path.read_bytes())


def sorted_ids_sha256(session_ids: list[str]) -> str:
    """Permanent fingerprint of a session-id set: ids sorted, newline-joined."""
    return sha256_hexdigest("\n".join(sorted(session_ids)).encode("utf-8"))


def verify_frozen_files() -> list[dict]:
    """Hash-check every frozen file BEFORE any write; fail loudly on mismatch.

    Hex digests are compared case-insensitively: recorded values originate
    from PowerShell Get-FileHash (uppercase) while Python hashlib emits
    lowercase for the identical digest.
    """
    observations = []
    for spec in FROZEN_FILE_SPECS:
        path = BASE_DIR / spec["file"]
        current = file_sha256(path)
        if (
            spec["expected"] is not None
            and current.lower() != spec["expected"].lower()
        ):
            raise SystemExit(
                f"ERROR: frozen file changed: {spec['file']} "
                f"(expected {spec['expected']}, found {current}). Stopping."
            )
        observations.append(
            {
                "file": spec["file"],
                "recorded_sha256": spec["expected"],
                "recorded_source": spec["recorded_source"],
                "current_sha256": current,
                "match": True if spec["expected"] is not None else None,
            }
        )
    return observations


def load_test_session_ids(path: Path) -> list[str]:
    """Load and validate the frozen test-set ids (strictly read-only)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset") != DATASET_PATH.name:
        raise SystemExit(f"ERROR: {path.name}: unexpected dataset field.")
    ids = payload.get("session_ids")
    if not isinstance(ids, list) or payload.get("test_rows") != EXPECTED_TEST_ROWS:
        raise SystemExit(f"ERROR: {path.name}: unexpected test_rows value.")
    if len(ids) != EXPECTED_TEST_ROWS:
        raise SystemExit(
            f"ERROR: {path.name}: expected {EXPECTED_TEST_ROWS} ids, "
            f"found {len(ids)}."
        )
    if len(set(ids)) != len(ids):
        duplicates = len(ids) - len(set(ids))
        raise SystemExit(
            f"ERROR: {path.name}: session ids are not unique "
            f"({duplicates} duplicate(s))."
        )
    return ids


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


def build_breakdown(
    rows: list[dict], labels: list[int], predictions: list[int]
) -> dict[str, dict[str, int]]:
    """Attack-type breakdown with the same shape/order as ml_results.json."""
    breakdown: dict[str, dict[str, int]] = {}
    for row, label, prediction in zip(rows, labels, predictions):
        cell = breakdown.setdefault(
            row["attack_type"], {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
        )
        if prediction == 1:
            cell["TP" if label == 1 else "FP"] += 1
        else:
            cell["TN" if label == 0 else "FN"] += 1
    ordered = (["none"] if "none" in breakdown else []) + sorted(
        t for t in breakdown if t != "none"
    )
    return {t: breakdown[t] for t in ordered}


def main() -> None:
    # --- Frozen-file hash gate (nothing is written before this passes). ---
    observations = verify_frozen_files()
    current_hashes = {o["file"]: o["current_sha256"] for o in observations}

    # --- Load the frozen test-set ids (read-only). ---
    ids = load_test_session_ids(SESSION_IDS_PATH)
    requested = set(ids)
    fingerprint = sorted_ids_sha256(ids)

    # --- Load and filter the dataset to the exact test rows. ---
    rows = load_rows(DATASET_PATH)
    matched = [row for row in rows if row["session_id"] in requested]
    matched_ids = {row["session_id"] for row in matched}
    missing = len(requested - matched_ids)
    duplicates = len(matched) - len(matched_ids)
    symmetric_difference = len(requested ^ matched_ids)
    if (
        len(matched) != EXPECTED_TEST_ROWS
        or missing != 0
        or duplicates != 0
        or symmetric_difference != 0
    ):
        raise SystemExit(
            "ERROR: test-row set mismatch "
            f"(matched={len(matched)}, missing={missing}, "
            f"duplicates={duplicates}, "
            f"symmetric_difference={symmetric_difference}). No files were written."
        )

    labels = [row["label"] for row in matched]
    label_counts = {0: labels.count(0), 1: labels.count(1)}
    if label_counts != EXPECTED_TEST_LABEL_COUNTS:
        raise SystemExit(
            f"ERROR: class distribution mismatch: {label_counts} "
            f"(expected {EXPECTED_TEST_LABEL_COUNTS}). No files were written."
        )

    # --- Frozen baseline scoring (imported score_session, unmodified). ---
    predictions = [
        score_session(
            row["geo_velocity_kmh"],
            row["device_mismatch_score"],
            row["token_reuse_flag"],
            row["login_burst_count"],
        )[2]
        for row in matched
    ]
    breakdown = build_breakdown(matched, labels, predictions)
    derived_matrix = {
        "TP": sum(c["TP"] for c in breakdown.values()),
        "FP": sum(c["FP"] for c in breakdown.values()),
        "TN": sum(c["TN"] for c in breakdown.values()),
        "FN": sum(c["FN"] for c in breakdown.values()),
    }

    # --- Frozen metric formulas (imported evaluate, unmodified). ---
    evaluation = evaluate_frozen_baseline(matched)
    if evaluation["confusion_matrix"] != derived_matrix:
        raise SystemExit(
            "ERROR: predictions-derived confusion matrix does not match the "
            "frozen evaluate() output. No files were written."
        )

    # --- Isolation Forest results: read-only consistency verification. ---
    ml_results = json.loads(ML_RESULTS_PATH.read_text(encoding="utf-8"))
    mismatches = []
    if ml_results["sample_count"] != EXPECTED_TEST_ROWS:
        mismatches.append(f"sample_count={ml_results['sample_count']}")
    if ml_results["split"]["test_rows"] != EXPECTED_TEST_ROWS:
        mismatches.append(f"split.test_rows={ml_results['split']['test_rows']}")
    if ml_results["split"]["test_session_ids_file"] != SESSION_IDS_PATH.name:
        mismatches.append("split.test_session_ids_file")
    if (
        ml_results["class_distribution"]["label_0"] != label_counts[0]
        or ml_results["class_distribution"]["label_1"] != label_counts[1]
    ):
        mismatches.append("class_distribution")
    observed_type_counts = Counter(row["attack_type"] for row in matched)
    if set(ml_results["breakdown_by_attack_type"]) != set(observed_type_counts):
        mismatches.append("breakdown_by_attack_type keys")
    else:
        for attack_type, n in observed_type_counts.items():
            if (
                sum(ml_results["breakdown_by_attack_type"][attack_type].values())
                != n
            ):
                mismatches.append(f"breakdown_by_attack_type[{attack_type}] rows")
    if mismatches:
        raise SystemExit(
            "ERROR: ml_results.json inconsistencies: "
            + "; ".join(mismatches)
            + ". No files were written."
        )

    base_metrics = evaluation["metrics"]
    ml_metrics = ml_results["metrics"]
    metrics_comparison = {
        key: {
            "baseline": base_metrics[key],
            "isolation_forest": ml_metrics[key],
            "delta_ml_minus_baseline": ml_metrics[key] - base_metrics[key],
        }
        for key in ["precision", "recall", "f1", "false_positive_rate"]
    }
    breakdown_comparison = {
        attack_type: {
            "n": sum(cell.values()),
            "baseline": cell,
            "isolation_forest": ml_results["breakdown_by_attack_type"][attack_type],
        }
        for attack_type, cell in breakdown.items()
    }

    verification = {
        "dataset_filename": DATASET_PATH.name,
        "test_set_filename": SESSION_IDS_PATH.name,
        "test_set_row_count": len(ids),
        "unique_session_id_count": len(requested),
        "class_distribution": {
            "label_0": label_counts[0],
            "label_1": label_counts[1],
        },
        "sorted_session_ids_sha256": fingerprint,
        "frozen_file_sha256": {
            o["file"]: o["current_sha256"] for o in observations
        },
        "frozen_file_hash_verification": observations,
        "frozen_file_hash_comparison_note": (
            "SHA-256 hex digests are case-insensitive; recorded values come "
            "from PowerShell Get-FileHash (uppercase) and current values from "
            "Python hashlib (lowercase); matching is case-insensitive."
        ),
        "checks": {
            "missing_ids": missing,
            "duplicate_dataset_matches": duplicates,
            "symmetric_difference": symmetric_difference,
            "class_distribution_matches_phase5_preview": True,
            "ml_results_consistency": {
                "sample_count": ml_results["sample_count"],
                "split_test_rows": ml_results["split"]["test_rows"],
                "class_distribution_match": True,
                "session_set_hash_in_ml_results": "not available",
                "mismatches": [],
            },
        },
        "frozen_files_read_only": True,
        "isolation_forest_rerun": False,
        "identical_session_id_set": True,
    }

    baseline_on_test = {
        "model": "rule_based_baseline",
        "evaluation_set": "held_out_test_set",
        "dataset": DATASET_PATH.name,
        "test_set": {
            "session_ids_file": SESSION_IDS_PATH.name,
            "session_ids_file_sha256": current_hashes[
                "ml_test_set_session_ids.json"
            ],
            "sorted_session_ids_sha256": fingerprint,
            "test_rows": len(matched),
        },
        "sample_count": evaluation["sample_count"],
        "class_distribution": evaluation["class_distribution"],
        "weights": evaluation["weights"],
        "normalization": evaluation["normalization"],
        "risk_tiers": evaluation["risk_tiers"],
        "tier_boundary_policy": evaluation["tier_boundary_policy"],
        "prediction_mapping": evaluation["prediction_mapping"],
        "prediction_semantics": evaluation["prediction_semantics"],
        "metrics": evaluation["metrics"],
        "confusion_matrix": evaluation["confusion_matrix"],
        "breakdown_by_attack_type": breakdown,
        "tier_distribution": evaluation["tier_distribution"],
        "frozen_inputs": {
            "scorer_module": "app.services.baseline_scorer",
            "scorer_function": "score_session",
            "scorer_sha256": current_hashes["app/services/baseline_scorer.py"],
            "metrics_function": (
                "evaluate_baseline.evaluate (imported unmodified)"
            ),
            "metrics_module_sha256": current_hashes["evaluate_baseline.py"],
        },
        "design_notes": (
            "Frozen rule-based baseline (unmodified) re-applied to the Phase 5 "
            "held-out test set (the exact 400 session ids listed in "
            "ml_test_set_session_ids.json); scoring via the imported frozen "
            "score_session; metrics via the imported frozen "
            "evaluate_baseline.evaluate (identical formulas and "
            "zero-denominator policy); weights and parameters are the frozen "
            "heuristics from baseline_scorer.py."
        ),
    }

    comparison = {
        "report": "baseline_vs_isolation_forest",
        "evaluation_set": {
            "description": "held-out test set (same 400 rows for both models)",
            "dataset": DATASET_PATH.name,
            "test_set_file": SESSION_IDS_PATH.name,
            "test_rows": len(matched),
            "sorted_session_ids_sha256": fingerprint,
            "class_distribution": {
                "label_0": label_counts[0],
                "label_1": label_counts[1],
            },
        },
        "metrics_comparison": metrics_comparison,
        "confusion_matrix_comparison": {
            "baseline": evaluation["confusion_matrix"],
            "isolation_forest": ml_results["confusion_matrix"],
        },
        "attack_type_breakdown_comparison": breakdown_comparison,
        "verification": verification,
    }

    verified_count = sum(1 for o in observations if o["match"] is True)
    first_time_count = len(observations) - verified_count

    print(
        f"Test set: {len(ids)} ids ({len(requested)} unique) | "
        f"matched rows: {len(matched)} | missing: {missing} | "
        f"duplicates: {duplicates} | symmetric difference: {symmetric_difference}"
    )
    print(
        f"Class distribution (label0/label1): {label_counts[0]} / "
        f"{label_counts[1]}"
    )
    print(f"Sorted session-ID SHA-256: {fingerprint}")
    print(
        f"Frozen files: {verified_count} verified against recorded hashes; "
        f"{first_time_count} recorded first-time (no prior value)"
    )
    print("Baseline on test set (frozen functions, imported unmodified):")
    print(f"  precision:            {base_metrics['precision']:.4f}")
    print(f"  recall:               {base_metrics['recall']:.4f}")
    print(f"  f1:                   {base_metrics['f1']:.4f}")
    print(f"  false_positive_rate:  {base_metrics['false_positive_rate']:.4f}")
    print("Isolation Forest (read from ml_results.json; not re-run):")
    print(f"  precision:            {ml_metrics['precision']:.4f}")
    print(f"  recall:               {ml_metrics['recall']:.4f}")
    print(f"  f1:                   {ml_metrics['f1']:.4f}")
    print(f"  false_positive_rate:  {ml_metrics['false_positive_rate']:.4f}")
    print("Delta (isolation_forest - baseline):")
    for key in ["precision", "recall", "f1", "false_positive_rate"]:
        entry = metrics_comparison[key]
        print(f"  {key:<20} {entry['delta_ml_minus_baseline']:+.4f}")
    print(
        "Breakdown by attack type "
        "(baseline TP/FN | isolation_forest TP/FN):"
    )
    print("  attack_type             n  baseTP  baseFN    ifTP    ifFN")
    for attack_type, entry in breakdown_comparison.items():
        b = entry["baseline"]
        m = entry["isolation_forest"]
        print(
            f"  {attack_type:<20} {entry['n']:>4}  {b['TP']:>6}  {b['FN']:>6}  "
            f"{m['TP']:>6}  {m['FN']:>6}"
        )

    metric_display = [
        ("Precision", "precision"),
        ("Recall", "recall"),
        ("F1", "f1"),
        ("False positive rate", "false_positive_rate"),
    ]
    md_lines = [
        "# ATE - Baseline vs Isolation Forest (Held-out Test Set Comparison)",
        "",
        f"Evaluation set: {len(matched)} rows from `{DATASET_PATH.name}` "
        "(identical session-ID set for both models; test-set file "
        f"`{SESSION_IDS_PATH.name}`).",
        f"Sorted session-ID SHA-256: `{fingerprint}`",
        "",
        "## Metrics",
        "",
        "| Metric | Baseline (frozen rule-based) | Isolation Forest | "
        "Delta (ML - Baseline) |",
        "|---|---:|---:|---:|",
    ]
    for name, key in metric_display:
        entry = metrics_comparison[key]
        md_lines.append(
            f"| {name} | {entry['baseline']:.4f} | "
            f"{entry['isolation_forest']:.4f} | "
            f"{entry['delta_ml_minus_baseline']:+.4f} |"
        )
    md_lines += [
        "",
        "## Confusion matrix (labeled, same 400 rows)",
        "",
        "| Cell | Baseline | Isolation Forest |",
        "|---|---:|---:|",
    ]
    bcm = evaluation["confusion_matrix"]
    mcm = ml_results["confusion_matrix"]
    for cell in ["TN", "FP", "FN", "TP"]:
        md_lines.append(f"| {cell} | {bcm[cell]} | {mcm[cell]} |")
    md_lines += [
        "",
        f"Actual classes: label 0 = {label_counts[0]} rows, "
        f"label 1 = {label_counts[1]} rows.",
        "",
        "## Attack-type breakdown (same 400 rows)",
        "",
        "| attack_type | n | Baseline TP | Baseline FN | "
        "Isolation Forest TP | Isolation Forest FN |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for attack_type, entry in breakdown_comparison.items():
        b = entry["baseline"]
        m = entry["isolation_forest"]
        md_lines.append(
            f"| {attack_type} | {entry['n']} | {b['TP']} | {b['FN']} | "
            f"{m['TP']} | {m['FN']} |"
        )
    none_entry = breakdown_comparison.get("none")
    if none_entry is not None:
        md_lines += [
            "",
            f"Normal rows (attack_type = none): baseline FP = "
            f"{none_entry['baseline']['FP']}, Isolation Forest FP = "
            f"{none_entry['isolation_forest']['FP']}.",
        ]
    md_lines += [
        "",
        "## Verification summary",
        "",
        f"- Test-set ids: {len(ids)} total, {len(requested)} unique; "
        f"dataset rows matched: {len(matched)}; missing: {missing}; "
        f"duplicates: {duplicates}; "
        f"symmetric difference: {symmetric_difference}.",
        f"- Class distribution (label 0 / label 1): {label_counts[0]} / "
        f"{label_counts[1]}.",
        "- Frozen files verified by SHA-256 against previously recorded "
        "values (see `comparison_report.json` -> `verification`).",
        "- Isolation Forest was not re-run; its results were read from "
        "`ml_results.json`.",
        "",
    ]

    BASELINE_ON_TEST_PATH.write_text(
        json.dumps(baseline_on_test, indent=2) + "\n", encoding="utf-8"
    )
    COMPARISON_JSON_PATH.write_text(
        json.dumps(comparison, indent=2) + "\n", encoding="utf-8"
    )
    COMPARISON_MD_PATH.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Baseline-on-test results written to: {BASELINE_ON_TEST_PATH}")
    print(f"Comparison report (JSON) written to: {COMPARISON_JSON_PATH}")
    print(f"Comparison report (MD) written to: {COMPARISON_MD_PATH}")


if __name__ == "__main__":
    main()
