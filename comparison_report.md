# ATE - Baseline vs Isolation Forest (Held-out Test Set Comparison)

Evaluation set: 400 rows from `ate_synthetic_dataset.csv` (identical session-ID set for both models; test-set file `ml_test_set_session_ids.json`).
Sorted session-ID SHA-256: `41dd6abc32f20171dd34db46217f99f3cfac1f9f3c5721fff613f64813c5e095`

## Metrics

| Metric | Baseline (frozen rule-based) | Isolation Forest | Delta (ML - Baseline) |
|---|---:|---:|---:|
| Precision | 1.0000 | 1.0000 | +0.0000 |
| Recall | 0.3125 | 0.9750 | +0.6625 |
| F1 | 0.4762 | 0.9873 | +0.5112 |
| False positive rate | 0.0000 | 0.0000 | +0.0000 |

## Confusion matrix (labeled, same 400 rows)

| Cell | Baseline | Isolation Forest |
|---|---:|---:|
| TN | 320 | 320 |
| FP | 0 | 0 |
| FN | 55 | 2 |
| TP | 25 | 78 |

Actual classes: label 0 = 320 rows, label 1 = 80 rows.

## Attack-type breakdown (same 400 rows)

| attack_type | n | Baseline TP | Baseline FN | Isolation Forest TP | Isolation Forest FN |
|---|---:|---:|---:|---:|---:|
| none | 320 | 0 | 0 | 0 | 0 |
| credential_stuffing | 22 | 1 | 21 | 22 | 0 |
| device_takeover | 23 | 11 | 12 | 23 | 0 |
| impossible_travel | 15 | 0 | 15 | 13 | 2 |
| token_replay | 20 | 13 | 7 | 20 | 0 |

Normal rows (attack_type = none): baseline FP = 0, Isolation Forest FP = 0.

## Verification summary

- Test-set ids: 400 total, 400 unique; dataset rows matched: 400; missing: 0; duplicates: 0; symmetric difference: 0.
- Class distribution (label 0 / label 1): 320 / 80.
- Frozen files verified by SHA-256 against previously recorded values (see `comparison_report.json` -> `verification`).
- Isolation Forest was not re-run; its results were read from `ml_results.json`.
