# IEEE-CIS Fraud Detection — Offline Validation Findings

**Status:** diagnostic offline experiment, completed 2026-09-21.
**Scope:** this document reports results from a single deterministic run of
`validate_ieee_cis.py`. It is **not** a measurement of ATE's live performance and
does **not** validate ATE's trained Isolation Forest model, its baseline scorer,
its weighted risk formula, or any live component. It tests only whether the
*general signal categories* used by ATE carry fraud-discriminative information in
an independent, real-world benchmark dataset.

**Reproduce:** `python validate_ieee_cis.py` from the repository root.
Deterministic (fixed seed 42), prints its complete report, writes no files.

**Run environment:** host Python 3.13.7; pandas 3.0.5; numpy 2.5.2; scikit-learn 1.9.0.
The run completed with **no warnings and no errors**.

---

## 1. Dataset source

The official **IEEE-CIS Fraud Detection** competition dataset (Kaggle), stored as a
local copy. No third-party copy was used; the CSV files were read-only inputs and
were not modified.

## 2. Dataset file paths

- `data/ieee_cis/ieee-fraud-detection/train_transaction.csv` (683,351,067 bytes)
- `data/ieee_cis/ieee-fraud-detection/train_identity.csv` (26,529,680 bytes)

`test_transaction.csv`, `test_identity.csv`, and `sample_submission.csv` are present
in the same directory but were **not used** (the test files carry no labels).

## 3. Dataset dimensions

- `train_transaction.csv`: **590,540 rows × 394 columns** (9 columns loaded).
- `train_identity.csv`: **144,233 rows × 41 columns** (23 columns loaded).
- Merged analysis population: **144,233 rows**.

## 4. Merge methodology

- Inner join on `TransactionID` (the only shared key; unique and non-null on both sides).
- The script validates, before merging: required columns exist; `TransactionID` has no
  nulls and no duplicates in either file; `isFraud` ∈ {0, 1}; `TransactionDT` has no
  nulls (no numeric imputation policy — the script fails loudly instead).
- Merge is guarded with `validate="one_to_one"` and frozen row/fraud-count checks, so a
  changed dataset aborts the run instead of silently validating something else.
- The merge is performed in memory only; no derived dataset file is written.

## 5. Merge statistics

| Quantity | Value |
|---|---|
| Transaction rows before merge | 590,540 |
| Identity rows before merge | 144,233 |
| Identity rows with a transaction match | 144,233 of 144,233 |
| Transaction rows matched | 144,233 |
| Transaction rows unmatched (dropped) | 446,307 |
| Retention | **24.42%** |
| Merged rows | 144,233 |
| Fraud rate, unmatched rows (context) | **2.0939%** |

**Selection effect (documented limitation):** the merged population's fraud rate
(7.8470%, §6) is far above the unmatched rows' 2.0939% — identity-record presence
correlates with fraud in this dataset, so the inner-joined analysis population is a
non-random 24.42% slice (corresponding overall training-file fraud rate ≈ 3.50%,
derived from the printed group rates). All findings below describe the matched subset only.

## 6. Class distribution

Merged population (144,233 rows):

| Class | Count | Share |
|---|---|---|
| Non-fraud (`isFraud=0`) | 132,915 | 92.1530% |
| Fraud (`isFraud=1`) | 11,318 | 7.8470% |

## 7. Exact columns used

29 feature columns + 1 target + 1 identifier:

- **Device:** `DeviceType`, `DeviceInfo`
- **Identity/browser:** `id_19`, `id_20`, `id_21`, `id_22`, `id_23`, `id_24`, `id_25`,
  `id_26`, `id_27`, `id_28`, `id_29`, `id_30`, `id_31`, `id_32`, `id_33`, `id_34`,
  `id_35`, `id_36`, `id_37`, `id_38` (all 20 exist)
- **Temporal:** `TransactionDT`
- **Card/account:** `card1`, `card2`, `card3`, `card4`, `card5`, `card6`
- **Target:** `isFraud` (never a feature; used only for the stratified split and metrics)
- **Identifier:** `TransactionID` (join key only; never a feature)

No other columns of the 394/41 available were used.

## 8. Mapping of feature categories to ATE's conceptual signal

| Source column(s) | Signal category | ATE conceptual mapping | Comparability |
|---|---|---|---|
| `DeviceType`, `DeviceInfo` | Device | Device fingerprinting / recognition / device-change signal | **Directly comparable category** (per-transaction device descriptors; NOT ATE's actual fingerprint algorithm) |
| `id_19`–`id_38` | Identity / browser / fingerprint | Browser/identity contextual attributes that provide fingerprint-like information | **Directly comparable category** (no cross-session comparison possible) |
| `TransactionDT` | Temporal | Temporal behavior / velocity-like analysis | **Directly comparable category** (time only; NOT geo-velocity — no geography or distance exists in this dataset) |
| `card1`–`card6` | Card / account context | Account/payment-instrument contextual patterns | **Only loosely related** (not equivalent to ATE's session-frequency/burst implementation) |

**Not validated by this experiment:** ATE's geo-velocity calculation, JWT/JTI reuse
detection, refresh-token replay detection, token rotation validation, ATE's actual
device fingerprint algorithm, ATE's weighted risk-scoring formula, and ATE's
Isolation Forest model. No equivalence to these is claimed or implied.

## 9. Missing-data statistics

Measured on the merged analysis population (selected features):

| Column | Missing | % | Column | Missing | % |
|---|---|---|---|---|---|
| DeviceType | 3,423 | 2.37% | id_30 | 66,668 | 46.22% |
| DeviceInfo | 25,567 | 17.73% | id_31 | 3,951 | 2.74% |
| id_19 | 4,915 | 3.41% | id_32 | 66,647 | 46.21% |
| id_20 | 4,972 | 3.45% | id_33 | 70,944 | 49.19% |
| id_21 | 139,074 | 96.42% | id_34 | 66,428 | 46.06% |
| id_22 | 139,064 | 96.42% | id_35 | 3,248 | 2.25% |
| id_23 | 139,064 | 96.42% | id_36 | 3,248 | 2.25% |
| id_24 | 139,486 | 96.71% | id_37 | 3,248 | 2.25% |
| id_25 | 139,101 | 96.44% | id_38 | 3,248 | 2.25% |
| id_26 | 139,070 | 96.42% | TransactionDT | 0 | 0.00% |
| id_27 | 139,064 | 96.42% | card1 | 0 | 0.00% |
| id_28 | 3,255 | 2.26% | card2 | 902 | 0.63% |
| id_29 | 3,255 | 2.26% | card3 | 172 | 0.12% |
| | | | card4 | 184 | 0.13% |
| | | | card5 | 956 | 0.66% |
| | | | card6 | 178 | 0.12% |

## 10. Missing-data handling

- Every categorical/code column: NaN mapped to an **explicit `"__MISSING__"` level** —
  never imputed, never silently dropped.
- No numeric imputation was applied or needed: the only true numeric feature
  (`TransactionDT`) is 100% complete; the script fails loudly if that ever changes.
- **No columns were excluded.** Documented exclusion policy: exclude only at
  ≥ 99.5% missing (fewer than 721 non-null rows); nothing qualifies — the worst case
  (`id_24`, 96.71% missing) retains 4,747 non-null rows.

## 11. Sampling methodology

**None — the complete merged population was used (144,233 rows).** Sampling was
unnecessary: the merged subset is small enough to process entirely in memory, so the
full population was preferred for statistical completeness. Class proportions were
preserved by stratification in the split, not by sampling.

## 12. Train / test methodology

- 80/20 **stratified** split on `isFraud`, `test_size=0.2`, `random_state=42`.
- Train: 115,386 rows (fraud 9,054; 7.8467%). Test: 28,847 rows (fraud 2,264; 7.8483%).
- The held-out test set was used **exactly once**, for the diagnostic metrics; no
  model selection, no threshold tuning, no repeated evaluation.
- All encodings (one-hot levels, frequency maps) were fitted on the training split only.

## 13. Model configuration

- `RandomForestClassifier(n_estimators=200, random_state=42, n_jobs=-1)`; all other
  parameters at library defaults (`max_depth=None`, `max_features="sqrt"`,
  `bootstrap=True`, `class_weight=None`). Fixed before the run; **not tuned**.
- Feature matrix: **98 columns** — 84 one-hot columns (15 source columns with ≤ 30
  training levels), 13 frequency-encoded columns (train-derived prevalence, non-ordinal;
  missing participates as its own level; test-unseen values → 0), and 1 numeric column
  (`TransactionDT`). No scaling, no derived features.
- Prediction threshold fixed at 0.5.

## 14. Feature importance ranking

Impurity-based `feature_importances_` of the train-fitted model; one-hot siblings are
summed back into their source column. All 29 features are shown (nothing cherry-picked).

| Rank | Feature | Category | Importance |
|---|---|---|---|
| 1 | TransactionDT | temporal | 0.211172 |
| 2 | id_20 | identity/browser | 0.105938 |
| 3 | id_19 | identity/browser | 0.102229 |
| 4 | card1 | card/account | 0.091702 |
| 5 | id_31 | identity/browser | 0.081398 |
| 6 | DeviceInfo | device | 0.076940 |
| 7 | card2 | card/account | 0.072855 |
| 8 | card5 | card/account | 0.037833 |
| 9 | id_33 | identity/browser | 0.025542 |
| 10 | id_30 | identity/browser | 0.025221 |
| 11 | card3 | card/account | 0.024790 |
| 12 | card6 | card/account | 0.018924 |
| 13 | card4 | card/account | 0.018499 |
| 14 | id_38 | identity/browser | 0.015292 |
| 15 | DeviceType | device | 0.012410 |
| 16 | id_29 | identity/browser | 0.011976 |
| 17 | id_37 | identity/browser | 0.011354 |
| 18 | id_28 | identity/browser | 0.009901 |
| 19 | id_34 | identity/browser | 0.009758 |
| 20 | id_32 | identity/browser | 0.007970 |
| 21 | id_35 | identity/browser | 0.007784 |
| 22 | id_36 | identity/browser | 0.005576 |
| 23 | id_24 | identity/browser | 0.003155 |
| 24 | id_26 | identity/browser | 0.002614 |
| 25 | id_23 | identity/browser | 0.002259 |
| 26 | id_25 | identity/browser | 0.002106 |
| 27 | id_21 | identity/browser | 0.001958 |
| 28 | id_22 | identity/browser | 0.001740 |
| 29 | id_27 | identity/browser | 0.001102 |

Sum of grouped importances: 1.000000.

**Aggregation by signal category** (sum of member feature importances; method documented):

| Category | Importance | Share |
|---|---|---|
| identity_browser (directly comparable) | 0.434875 | 43.49% |
| card_account (loosely related) | 0.264603 | 26.46% |
| temporal (directly comparable) | 0.211172 | 21.12% |
| device (directly comparable) | 0.089350 | 8.94% |

## 15. Diagnostic performance metrics

Held-out test set (28,847 rows, 2,264 fraud), fixed 0.5 threshold:

| Metric | Value |
|---|---|
| ROC-AUC | **0.9271** |
| Precision | 0.8871 |
| Recall | 0.5239 |
| F1 | 0.6587 |
| Confusion matrix | TP=1,186; FP=151; TN=26,432; FN=1,078 |

Reference points: a random classifier has ROC-AUC 0.5; a majority-class predictor
scores precision/recall/F1 = 0.0000 under the zero-denominator policy. No threshold
tuning was performed (fixed at 0.5), which explains the high-precision /
moderate-recall operating point.

**These metrics describe the diagnostic benchmark experiment only.** They are not
ATE's performance, not a new ATE benchmark, and not comparable to ATE's baseline
scorer (different data, different features, different task).

## 16. Limitations

1. **No geography or absolute time.** `TransactionDT` is a relative monotonic offset;
   no location data exists, so ATE's geo-velocity mechanism cannot be tested here.
   Part of TransactionDT's importance may reflect temporal fraud drift in the dataset
   rather than per-user behavioral velocity.
2. **No token concepts.** JWT/JTI reuse, refresh-token replay, and token rotation are
   not representable in this dataset.
3. **Device fields are per-transaction descriptors**, not cross-session fingerprint
   comparisons — device-*change* semantics are not tested.
4. **Selection effect.** The inner join keeps only 24.42% of transactions; that subset
   has a 7.85% fraud rate vs 2.09% among excluded rows. Findings describe the matched
   subset only.
5. **Encoding surrogates.** High-cardinality columns are frequency-encoded (prevalence),
   so individual category identities are not resolved. No ordinal conversion was used.
6. **Importance caveats.** Impurity-based importance is biased toward continuous /
   high-cardinality features and indicates association, not causation; grouped sums are
   an aggregation choice, documented above.
7. **Single fixed model and split.** One diagnostic model with documented defaults and
   one stratified split; not a performance-maximizing benchmark.
8. **Domain shift.** IEEE-CIS covers card-not-present e-commerce transactions from a
   different period and domain than fintech login/session security.
9. **Metrics are threshold-dependent.** Precision/recall/F1 reflect the fixed 0.5 cut;
   ROC-AUC is threshold-free. No tuning was done by design.

## 17. Honest interpretation

- **Identity/browser attributes (`id_19`–`id_38`) carry substantial signal** — the
  largest aggregate share (43.49%); `id_20` and `id_19` rank 2–3 of all features.
- **Temporal information is strongly informative** — `TransactionDT` is the single
  most important feature (21.12%), with the drift caveat in §16.1.
- **Card/account context is informative but only loosely related** to ATE's categories
  (26.46%; `card1` ranks 4th at 9.17%).
- **Device-related information is the weakest category** (8.94%; DeviceInfo 7.69%,
  DeviceType 1.24%) — measurable but modest, and not a test of fingerprint algorithms.
- Several sparse identity columns (`id_21`–`id_27`) contribute near-zero importance
  individually; this is reported, not hidden.
- Overall the results are **supportive — with the documented caveats above — that the
  general signal categories carry fraud-relevant information**, and they are not
  uniformly strong (device is clearly the weakest). Nothing here validates ATE's
  specific implementations or live behavior.

## 18. What this supports vs what it does not validate

**This experiment supports (evidence):**
- Device-related information, identity/browser attributes, and transaction-time
  information each carry measurable, reproducible fraud-discriminative signal in an
  independent real-world benchmark, measured on a held-out split
  (ROC-AUC 0.9271).

**This experiment does NOT validate:**
- ATE's geo-velocity calculation · JWT/JTI reuse detection · refresh-token replay
  detection · token rotation validation · ATE's actual device fingerprint algorithm ·
  ATE's weighted risk-scoring formula · ATE's Isolation Forest model · ATE's live or
  production performance of any kind.

**Conclusion (the research question):** *Do these independent IEEE-CIS results provide
evidence that ATE's general device, identity/fingerprint, and temporal signal
categories carry fraud-relevant information?* — **Yes, supportive**: identity/browser
attributes dominate importance (43.49%), temporal information is the strongest single
feature (21.12%), and device-related descriptors are present but the weakest category
(8.94%), with a held-out ROC-AUC of 0.9271 under fixed, untuned settings. These
findings describe the diagnostic benchmark experiment only.
