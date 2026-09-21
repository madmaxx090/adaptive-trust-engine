"""IEEE-CIS offline validation - feature-category signal check (diagnostic only).

Standalone research-credibility experiment. NOT part of the live ATE system,
NOT a production model, and NOT a benchmark of ATE's baseline scorer or its
Isolation Forest.

What this script does:
- reads the official IEEE-CIS Fraud Detection competition files
  (data/ieee_cis/ieee-fraud-detection/train_transaction.csv + train_identity.csv;
  read-only, never modified)
- validates the schema and the inner join on TransactionID (fails loudly)
- maps 29 columns to ATE's conceptual signal categories:
  device / identity-browser / temporal / card-account
- builds a deterministic feature matrix: explicit "__MISSING__" category,
  one-hot for <= 30 training levels, frequency encoding above (train-only)
- 80/20 stratified split (random_state=42); the held-out test set is used
  exactly once, for the diagnostic metrics
- fits a Random Forest with fixed, documented defaults for the
  feature-importance ranking
- reports ROC-AUC / F1 / precision / recall as supporting context only

Leakage controls:
- frequency maps and one-hot levels are derived from the training split only
- isFraud never enters the feature matrix; TransactionID is an identifier only
- no threshold tuning, no model selection, no repeated test-set use
- test_transaction.csv / test_identity.csv are never read (no labels there)

Reproducibility: fixed seed 42; frozen dataset guards; every step prints its
numbers; no hidden preprocessing and no files written.
"""

import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

# ---------------------------------------------------------------------------
# Configuration (fixed before the run - do not tune)
# ---------------------------------------------------------------------------
SEED = 42
TEST_SIZE = 0.2
N_ESTIMATORS = 200            # fixed for stable importance estimates; not tuned
ONE_HOT_MAX_LEVELS = 30       # <= level count -> one-hot; above -> frequency encoding
SPARSE_EXCLUSION_PCT = 99.5   # exclusion policy; expected result: nothing excluded
MISSING_LEVEL = "__MISSING__"
CLASSIFIER_THRESHOLD = 0.5    # fixed; never tuned against the test set

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "ieee_cis" / "ieee-fraud-detection"
TRANSACTION_PATH = DATA_DIR / "train_transaction.csv"
IDENTITY_PATH = DATA_DIR / "train_identity.csv"

TX_COLUMNS = ["TransactionID", "isFraud", "TransactionDT",
              "card1", "card2", "card3", "card4", "card5", "card6"]
ID_COLUMNS = ["TransactionID", "DeviceType", "DeviceInfo"] + [f"id_{i}" for i in range(19, 39)]

FEATURE_COLUMNS = (["DeviceType", "DeviceInfo"] + [f"id_{i}" for i in range(19, 39)]
                   + ["TransactionDT", "card1", "card2", "card3", "card4", "card5", "card6"])
CATEGORICAL_COLUMNS = [c for c in FEATURE_COLUMNS if c != "TransactionDT"]

SIGNAL_CATEGORIES = {
    "device (directly comparable)": ["DeviceType", "DeviceInfo"],
    "identity_browser (directly comparable)": [f"id_{i}" for i in range(19, 39)],
    "temporal (directly comparable)": ["TransactionDT"],
    "card_account (loosely related)":
        ["card1", "card2", "card3", "card4", "card5", "card6"],
}

# Frozen dataset guards (verified during the Step-17 inspection; the script
# must fail loudly if the files ever change instead of silently validating a
# different dataset).
EXPECTED_TRANSACTION_ROWS = 590_540
EXPECTED_IDENTITY_ROWS = 144_233
EXPECTED_MERGED_ROWS = 144_233
EXPECTED_FRAUD_COUNT = 20_663


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def read_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        return next(csv.reader(fh))


def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the two training files (selected columns) with schema validation."""
    for path in (TRANSACTION_PATH, IDENTITY_PATH):
        if not path.exists():
            fail(f"{path} not found. The IEEE-CIS dataset must be present locally.")

    for path, required in ((TRANSACTION_PATH, TX_COLUMNS), (IDENTITY_PATH, ID_COLUMNS)):
        header = read_header(path)
        missing = [c for c in required if c not in header]
        if missing:
            fail(f"{path.name}: missing required column(s): {missing}")

    transaction = pd.read_csv(TRANSACTION_PATH, usecols=TX_COLUMNS)
    identity = pd.read_csv(IDENTITY_PATH, usecols=ID_COLUMNS)

    for name, df in (("train_transaction.csv", transaction), ("train_identity.csv", identity)):
        if df["TransactionID"].isna().any():
            fail(f"{name}: null TransactionID values found.")
        if df["TransactionID"].duplicated().any():
            fail(f"{name}: duplicate TransactionID values found.")

    if not set(transaction["isFraud"].unique()) <= {0, 1}:
        fail("train_transaction.csv: isFraud must contain only 0/1.")
    if transaction["TransactionDT"].isna().any():
        fail("train_transaction.csv: null TransactionDT values found "
             "(no numeric imputation policy - failing instead of guessing).")

    if len(transaction) != EXPECTED_TRANSACTION_ROWS:
        fail(f"train_transaction.csv: expected {EXPECTED_TRANSACTION_ROWS:,} rows, "
             f"found {len(transaction):,} - the dataset changed. Stopping.")
    if len(identity) != EXPECTED_IDENTITY_ROWS:
        fail(f"train_identity.csv: expected {EXPECTED_IDENTITY_ROWS:,} rows, "
             f"found {len(identity):,} - the dataset changed. Stopping.")
    return transaction, identity


def merge_data(transaction: pd.DataFrame, identity: pd.DataFrame) -> pd.DataFrame:
    """Inner join on TransactionID with full merge reporting."""
    print("\n=== MERGE (inner join on TransactionID) ===")
    print(f"transaction rows: {len(transaction):,} | identity rows: {len(identity):,}")

    matched = transaction["TransactionID"].isin(set(identity["TransactionID"]))
    print(f"identity rows with a transaction match: "
          f"{int(identity['TransactionID'].isin(set(transaction['TransactionID'])).sum()):,}"
          f" of {len(identity):,}")

    try:
        merged = transaction.merge(identity, on="TransactionID", how="inner",
                                   validate="one_to_one")
    except pd.errors.MergeError as exc:
        fail(f"inner join on TransactionID failed: {exc}")

    if len(merged) != EXPECTED_MERGED_ROWS:
        fail(f"expected {EXPECTED_MERGED_ROWS:,} merged rows, found {len(merged):,}. Stopping.")
    if int(transaction["isFraud"].sum()) != EXPECTED_FRAUD_COUNT:
        fail("train_transaction.csv: total fraud count changed. Stopping.")

    lost = int((~matched).sum())
    print(f"transaction rows matched: {int(matched.sum()):,} | unmatched: {lost:,} "
          f"| retention: {matched.mean():.2%}")
    print(f"merged rows: {len(merged):,}")
    print(f"unmatched-only fraud rate (context for the selection effect): "
          f"{transaction.loc[~matched, 'isFraud'].mean():.4%}")
    return merged


def print_class_distribution(merged: pd.DataFrame) -> None:
    counts = merged["isFraud"].value_counts()
    total = len(merged)
    print("\n=== CLASS DISTRIBUTION (merged population) ===")
    print(f"non-fraud: {int(counts.get(0, 0)):,} ({counts.get(0, 0) / total:.4%})")
    print(f"fraud:     {int(counts.get(1, 0)):,} ({counts.get(1, 0) / total:.4%})")


def report_missingness(df: pd.DataFrame, columns: list[str]) -> dict[str, float]:
    print("\n=== MISSINGNESS (selected features) ===")
    missing_pct: dict[str, float] = {}
    for col in columns:
        pct = float(df[col].isna().mean() * 100.0)
        missing_pct[col] = pct
        print(f"{col:<14} missing={int(df[col].isna().sum()):>7,} ({pct:6.2f}%)")
    return missing_pct


def apply_exclusion_policy(columns: list[str],
                           missing_pct: dict[str, float]) -> list[str]:
    excluded = [c for c in columns if missing_pct[c] >= SPARSE_EXCLUSION_PCT]
    detail = ", ".join(excluded) if excluded else "none qualify - no columns excluded"
    print(f"\nsparse-exclusion policy (>={SPARSE_EXCLUSION_PCT}% missing): {detail}")
    return [c for c in columns if c not in excluded]


def build_string_frame(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Categorical/code columns -> string with an explicit missing level."""
    features = df[columns].copy()
    for col in columns:
        if col in CATEGORICAL_COLUMNS:
            features[col] = features[col].fillna(MISSING_LEVEL).astype(str)
    return features


def encode(train_rows: pd.DataFrame, test_rows: pd.DataFrame, columns: list[str]):
    """Fit encodings on the training split only (leakage control)."""
    x_train = pd.DataFrame(index=train_rows.index)
    x_test = pd.DataFrame(index=test_rows.index)
    groups: dict[str, list[str]] = {}
    report: list[tuple[str, str, str]] = []

    for col in columns:
        if col == "TransactionDT":
            x_train[col] = train_rows[col].astype("float64")
            x_test[col] = test_rows[col].astype("float64")
            groups[col] = [col]
            report.append((col, "numeric", "-"))
            continue

        train_col = train_rows[col]
        test_col = test_rows[col]
        levels = int(train_col.nunique())

        if levels <= ONE_HOT_MAX_LEVELS:
            train_dummies = pd.get_dummies(train_col, prefix=col, dtype=int)
            test_dummies = pd.get_dummies(test_col, prefix=col, dtype=int).reindex(
                columns=train_dummies.columns, fill_value=0)
            for name in train_dummies.columns:
                x_train[name] = train_dummies[name]
                x_test[name] = test_dummies[name]
            groups[col] = list(train_dummies.columns)
            report.append((col, f"one-hot ({len(train_dummies.columns)} cols)", str(levels)))
        else:
            frequency = train_col.value_counts(normalize=True)
            x_train[col] = train_col.map(frequency).astype("float64")
            x_test[col] = test_col.map(frequency).fillna(0.0).astype("float64")
            groups[col] = [col]
            report.append((col, "frequency", str(levels)))

    return x_train, x_test, groups, report


def classification_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    tn = int(((y_pred == 0) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    # Zero-denominator policy: undefined ratios are reported as 0.0 (documented).
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) > 0 else 0.0)
    return {"TP": tp, "FP": fp, "TN": tn, "FN": fn,
            "precision": precision, "recall": recall, "f1": f1}


def report_importance(model: RandomForestClassifier, feature_names,
                      groups: dict[str, list[str]], columns: list[str]) -> None:
    raw = pd.Series(model.feature_importances_, index=feature_names)
    source_importance = {col: float(raw[groups[col]].sum()) for col in columns}

    total = sum(source_importance.values())
    if abs(total - 1.0) > 1e-6:
        fail(f"grouped importances do not sum to 1.0 (got {total!r}).")

    print("\n=== FEATURE IMPORTANCE (impurity-based, grouped by source column) ===")
    ranked = sorted(source_importance.items(), key=lambda item: item[1], reverse=True)
    for rank, (col, value) in enumerate(ranked, 1):
        print(f"{rank:>2}. {col:<14} {value:.6f}")
    print(f"sum of grouped importances: {total:.6f} (expected 1.0)")

    print("\n=== SIGNAL-CATEGORY AGGREGATION (sum of member feature importances) ===")
    for category, members in SIGNAL_CATEGORIES.items():
        present = [m for m in members if m in source_importance]
        value = sum(source_importance[m] for m in present)
        print(f"{category:<45} {value:.6f} ({value * 100:.2f}%)")


def main() -> None:
    print("IEEE-CIS offline validation (diagnostic only; nothing is written)")
    print(f"dataset: {DATA_DIR}")

    transaction, identity = load_data()
    print("\n=== DATASET FILES ===")
    print(f"train_transaction.csv: {len(transaction):,} rows "
          f"({len(TX_COLUMNS)} of 394 columns loaded)")
    print(f"train_identity.csv:    {len(identity):,} rows "
          f"({len(ID_COLUMNS)} of 41 columns loaded)")

    merged = merge_data(transaction, identity)
    print_class_distribution(merged)

    missing_pct = report_missingness(merged, FEATURE_COLUMNS)
    selected = apply_exclusion_policy(FEATURE_COLUMNS, missing_pct)

    features = build_string_frame(merged, selected)
    labels = merged["isFraud"].to_numpy()

    train_idx, test_idx = train_test_split(
        np.arange(len(features)), test_size=TEST_SIZE, random_state=SEED, stratify=labels)

    print("\n=== SPLIT (stratified, deterministic) ===")
    print(f"train rows: {len(train_idx):,} | test rows: {len(test_idx):,} "
          f"(test_size={TEST_SIZE}, random_state={SEED}, stratify=isFraud)")
    print(f"train fraud: {int(labels[train_idx].sum()):,} ({labels[train_idx].mean():.4%}) | "
          f"test fraud: {int(labels[test_idx].sum()):,} ({labels[test_idx].mean():.4%})")

    x_train, x_test, groups, encoding_report = encode(
        features.iloc[train_idx], features.iloc[test_idx], selected)

    print("\n=== ENCODING (fitted on the train split only) ===")
    print(f"categorical NaN -> explicit '{MISSING_LEVEL}' level; "
          f"one-hot levels and frequency maps are train-derived")
    for col, method, levels in encoding_report:
        print(f"{col:<14} {method:<22} train_levels={levels}")
    print(f"feature matrix: train {x_train.shape[0]:,}x{x_train.shape[1]:,} | "
          f"test {x_test.shape[0]:,}x{x_test.shape[1]:,}")

    print("\n=== MODEL (fixed configuration, no tuning) ===")
    print(f"RandomForestClassifier(n_estimators={N_ESTIMATORS}, "
          f"random_state={SEED}, n_jobs=-1); all other parameters at library defaults")
    model = RandomForestClassifier(n_estimators=N_ESTIMATORS, random_state=SEED, n_jobs=-1)
    model.fit(x_train, labels[train_idx])

    probabilities = model.predict_proba(x_test)[:, 1]
    predictions = (probabilities >= CLASSIFIER_THRESHOLD).astype(int)
    metrics = classification_metrics(labels[test_idx], predictions)
    auc = roc_auc_score(labels[test_idx], probabilities)

    print("\n=== DIAGNOSTIC METRICS (held-out test set, threshold 0.5) ===")
    print(f"ROC-AUC:   {auc:.4f}")
    print(f"precision: {metrics['precision']:.4f}")
    print(f"recall:    {metrics['recall']:.4f}")
    print(f"F1:        {metrics['f1']:.4f}")
    print(f"confusion matrix: TP={metrics['TP']:,} FP={metrics['FP']:,} "
          f"TN={metrics['TN']:,} FN={metrics['FN']:,}")
    print("reference: random classifier ROC-AUC = 0.5; majority-class predictor "
          "gives precision/recall/F1 = 0.0000 (zero-denominator policy)")
    print("note: diagnostic benchmark only - not ATE production performance, "
          "not a new ATE benchmark, not a comparison with ATE's baseline scorer")

    report_importance(model, x_train.columns, groups, selected)

    print("\n=== VERSIONS ===")
    print(f"python {sys.version_info.major}.{sys.version_info.minor}."
          f"{sys.version_info.micro} | pandas {pd.__version__} | "
          f"numpy {np.__version__} | scikit-learn {sklearn.__version__}")
    print("\nVALIDATION COMPLETE (nothing written)")


if __name__ == "__main__":
    main()
