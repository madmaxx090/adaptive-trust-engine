# ATE (AdaptiveTrust Engine) — Project Context

Session-security backend for fintech applications (FYP). It scores login/session
events with live risk signals (geo velocity, device mismatch, token reuse,
login burst), applies a frozen rule-based baseline scorer as the decision layer,
and persists every scored session for audit and later analysis. An Isolation
Forest model was evaluated offline against the same rule-based reference and is
now also served **live** as an additional, un-fused signal (step 10).

Status: steps 1–10 complete plus the read endpoints (`GET /sessions`,
`GET /sessions/{session_id}`) — infrastructure, live pipeline, frozen scorer,
offline experiments (baseline + Isolation Forest), extended test suite, the
step 9 hardening fixes, and the live ML signal (load-once Isolation Forest
alongside the baseline). This document is the authoritative current-state
reference; `README.md` reflects the earlier skeleton phase.

## 1. Architecture

### Runtime topology (Docker Compose)

| Service | Image | Purpose | Host port |
|---|---|---|---|
| `api` | built from `Dockerfile` (python:3.11-slim) | FastAPI app + tests | 8008 → container 8000 |
| `postgres` | postgres:16-alpine | users, sessions, risk_events, audit_log | 5432 |
| `redis` | redis:7-alpine | rolling login-burst ZSET, refresh-token hash store | 6379 |

- GeoIP: an offline MaxMind GeoLite2-City database is **placed manually** at
  `data/geoip/GeoLite2-City.mmdb` (mounted read-only; never auto-downloaded).
  Without it the geo layer raises `GeoIPDatabaseUnavailableError` → 503.
- The api container gets `DATABASE_URL`/`REDIS_URL` pointing at the Compose
  service names (`postgres`, `redis`), never localhost.

### Scoring flow (`POST /session/score`)

1. Record the attempt in the Redis rolling burst ZSET `ate:login_burst:{user_id}`
   (60-second window; members are nanosecond-timestamped, so they stay unique).
2. Load the user's previous session (device fingerprint, IP, last seen) from Postgres.
3. Compute live signals:
   - **Geo velocity** — GeoLite2 lookups + haversine distance between the
     previous and current IP locations, divided by elapsed time (km/h).
     Location statuses: `ok` / `no_history` / `invalid_ip` / `private_ip` / `database_unavailable`.
   - **Device mismatch** — current `device_fingerprint` vs the previous session (0/1).
   - **Token reuse** — `SHA-256(refresh_token)` compared against the hash stored
     for the previous session in Redis (raw tokens are never stored).
   - **Login burst count** — attempts for this user in the rolling 60 s window.
4. Score with the frozen baseline scorer (unrounded raw score + tier).
5. Score with the live Isolation Forest signal (step 10): the artifact is
   loaded ONCE at application startup and the same four live features go
   through the frozen Phase 5 feature contract, yielding `ml_anomaly_flag`
   (Phase 5 convention: -1 → flagged) and `ml_decision_score` (raw
   `decision_function`; higher = more normal, negative = anomalous). The ML
   signal is exposed alongside, never fused with, the baseline. A
   missing/corrupt artifact fails startup; a request-time ML failure → 503
   (same sanitized body).
6. Persist atomically in Postgres, single transaction: user find-or-create
   (race-safe upsert — see §6), `sessions` row, `risk_events` row containing the
   score, tier, and `contributing_signals` including `risk_score_unrounded`.
   ML fields are response-only and are NOT persisted (this phase).
7. Post-commit Redis updates (store the current session's token hash, etc.) —
   **fail-soft**: a Redis error never turns an already-persisted session into
   an error response.
8. Respond with `risk_score` (rounded int), `risk_tier`, `session_id`,
   `contributing_signals`, `ml_anomaly_flag`, and `ml_decision_score` (the
   last two are additive; existing fields unchanged).

### Failure semantics

- GeoIP database unavailable, persistence failure, or ML-signal failure → **503**,
  with the fixed generic body `"Scoring service temporarily unavailable. Please try again later."`;
  the full exception is logged server-side only (step 9 hardening; the ML
  signal reuses the same body — step 10).
- ML artifact missing/corrupt/unreadable → application startup fails fast with
  a descriptive server-side error (never a silent fallback pretending the ML
  signal ran).
- Validation: 422 for missing/null/int/empty/oversized fields. Length caps:
  `user_id` ≤ 255, `ip_address` ≤ 45, `device_fingerprint` ≤ 255. A
  malformed-but-non-empty IP is *not* a validation error — it is scored (200)
  with `geo_location_status = "invalid_ip"`.

### File map

```
ate/
├── app/
│   ├── main.py                  FastAPI app; CORS (localhost dev origins); mounts routers;
│   │                            lifespan loads the ML artifact at startup (fail-fast)
│   ├── api/
│   │   ├── health.py            GET /health -> {"status": "ok"}
│   │   └── session.py           POST /session/score; sanitized 503 mapping (step 9);
│   │                            read-only GET /sessions + GET /sessions/{session_id}
│   ├── core/
│   │   ├── config.py            Pydantic Settings: DATABASE_URL, REDIS_URL, GEOIP_DB_PATH,
│   │   │                        ML_MODEL_PATH, CORS_ORIGINS
│   │   └── database.py          Sync SQLAlchemy engine + SessionLocal (psycopg2)
│   ├── models/
│   │   ├── user.py              users (id uuid PK, user_id unique, created_at)
│   │   ├── session.py           sessions (session_id unique, user FK, device_fingerprint,
│   │   │                        ip_address, created_at, last_seen_at)
│   │   ├── risk_event.py        risk_events (session FK, risk_score, risk_tier,
│   │   │                        contributing_signals JSONB)
│   │   ├── audit_log.py         audit_log (event_type, nullable session FK, details JSON)
│   │   └── base.py              declarative Base
│   ├── schemas/
│   │   ├── health.py            health response model
│   │   └── session.py           request/response models; min/max length constraints
│   │                            (step 9); session-read response schemas; additive
│   │                            ml_anomaly_flag / ml_decision_score (step 10)
│   └── services/
│       ├── risk_pipeline.py     live orchestration: signals -> baseline score -> ML score
│       │                        -> persist -> Redis; race-safe find-or-create (step 9)
│       ├── baseline_scorer.py   FROZEN rule-based scorer (formula + tiers)
│       ├── session_store.py     FROZEN Redis session/token-hash helpers
│       ├── isolation_forest_scorer.py  FROZEN Isolation Forest wrapper (offline evaluation)
│       ├── ml_runtime.py        live ML signal: load-once artifact + inference (step 10)
│       └── geo.py               GeoLite2 lookup + haversine geo-velocity computation
├── alembic/
│   └── versions/0001_initial_schema.py   initial migration: users, sessions, risk_events, audit_log
├── tests/                       70 tests (baked into the api image)
├── train_ml_model.py            host script: builds + verifies the live model artifact
│                                against the frozen ml_results.json (step 10)
├── ml_model/
│   ├── isolation_forest_v1.joblib             served artifact (SHA-256 in §4)
│   └── isolation_forest_v1_verification.json  SHA-256 + versions + golden samples
├── evaluate_baseline.py         FROZEN offline baseline evaluation script
├── baseline_results.json        FROZEN baseline experiment results
├── ml_results.json              FROZEN Isolation Forest experiment results
├── ml_test_set_session_ids.json FROZEN session ids of the ML test split
├── Dockerfile                   python:3.11-slim; COPY app/ tests/ ml_model/ alembic/ alembic.ini
├── docker-compose.yml           api + postgres + redis definitions
└── requirements.txt
```

## 2. Scorer formula, weights and tier rules (frozen)

Weights and normalization (from `baseline_results.json`, `baseline_scorer.py`):

| Signal | Weight | Normalization (0–100) |
|---|---|---|
| Geo velocity | 0.30 | `min(geo_velocity_kmh / 1000, 1) * 100` (cap: 1000 km/h) |
| Device mismatch | 0.30 | `device_mismatch_score * 100` (0/1) |
| Token reuse | 0.25 | `100 if token_reuse_flag else 0` |
| Login burst | 0.15 | `min(login_burst_count / 30, 1) * 100` (cap: 30 attempts / 60 s) |

Equivalent raw-score form:

```
raw = 30 * min(v/1000, 1) + 30 * dm + 25 * tok + 15 * min(burst/30, 1)
```

**Tier rules are applied to the UNROUNDED raw score:**

- `raw <= 40.0` → **low**
- `40.0 < raw <= 70.0` → **medium**
- `raw > 70.0` → **high**

**Rounded-score vs tier display mismatch:** the API's `risk_score` is the
rounded display value `int(round(raw))` (Python banker's rounding), while the
tier uses the unrounded raw value. At exact boundaries they can disagree —
e.g. `raw = 40.01` → tier `medium` but displayed score `40`; `raw = 70.01` →
tier `high` but displayed score `70`. This behavior is intentional (tier is
authoritative) and is pinned by a dedicated test. Note: `baseline_results.json`
lists display ranges ("0-40", "41-70", "71-100"); the authoritative boundary
policy is the unrounded rule above.

For classification metrics, `medium` and `high` are treated as flagged
(prediction 1); `low` is not flagged (prediction 0), for both the baseline and
the Isolation Forest evaluation.

## 3. How to run

Start the stack (from the repository root):

```bash
cp .env.example .env          # optional; Compose works with built-in defaults
docker compose up -d --build
```

Endpoints (host port 8008 → container 8000):

- `GET  http://localhost:8008/health`
- `POST http://localhost:8008/session/score`
- `GET  http://localhost:8008/sessions` — read-only list, newest first;
  `page` (≥ 1, default 1), `limit` (1–100, default 20), optional `risk_tier`
  (low/medium/high) filtered on each session's latest risk event; only scored
  sessions appear (event-less rows are excluded by design).
- `GET  http://localhost:8008/sessions/{session_id}` — read-only detail:
  latest event's score/tier/signals plus a chronological per-risk-event
  history; 404 for unknown (or event-less) sessions.

Fresh database (tables are created by Alembic):

```bash
docker compose exec -T api alembic upgrade head
```

Run the full test suite (inside the running stack, against live Postgres/Redis):

```bash
docker compose exec -T api python -m pytest -q
```

**Important — tests are baked into the image** (`Dockerfile`: `COPY tests ./tests`;
the only bind-mount is `./data/geoip`). After changing any test file (or app
code), rebuild before running:

```bash
docker compose build api
docker compose up -d api
```

Test suite layout (70 tests):

- `tests/test_health.py` (1) — health endpoint.
- `tests/test_session_score.py` (2) — scorer/session-store unit tests.
- `tests/test_live_signals.py` (14) — per-signal live behavior: geo statuses,
  token reuse, device mismatch, burst window, 503 paths (GeoIP DB missing,
  forced persistence failure).
- `tests/test_endpoint_validation.py` (24) — engineered tier boundaries,
  signal combinations, malformed input (422 vs scored-200), 503 sanitization,
  and real-HTTP concurrency (10 simultaneous requests, fresh and existing user).
- `tests/test_sessions_endpoints.py` (9) — read endpoints: list ground truth
  (independent ORM mirror), pagination partition, tier filter, 422/404
  contracts, detail vs persisted row, latest-event selection + per-event
  history, event-less exclusion.
- `tests/test_ml_signal.py` (20) — live ML signal: frozen-contract equivalence,
  determinism, load-once, invalid-feature errors, startup fail-fast
  (missing/corrupt artifact), golden cross-environment check (artifact SHA-256
  + version + 25 samples), measured latency, exact measured decision-score
  locks for six live scenarios, sanitized 503 on ML failure, response schema
  keys, and the printed baseline-vs-ML side-by-side table.

Some tests use a fake geolocation (documented monkeypatch) to keep results
deterministic; concurrency tests fire real HTTP at the in-container uvicorn
via a thread barrier.

## 4. Experimental results

Dataset: `ate_synthetic_dataset.csv` — 2000 rows, 20 % attack-labeled
(400 positives / 1600 negatives), four attack types: credential stuffing,
device takeover, impossible travel, token replay.

### Baseline (rule-based scorer, frozen) — all 2000 rows

| Metric | Value | Confusion | | |
|---|---|---|---|---|
| precision | 1.0 | TP | 98 | FP | 0 |
| recall | 0.245 | FN | 302 | TN | 1600 |
| f1 | 0.3936 | | | | |
| false positive rate | 0.0 | | | | |

Tier distribution: low 1902, medium 98, high 0. Design: fixed heuristic weights,
deterministic reference baseline (not learned/fitted/tuned).

### Isolation Forest (frozen) — 400-row stratified test split

Parameters: `contamination=0.2`, `n_estimators=100`, `random_state=42`;
unsupervised fit on training features only (labels never passed to `fit`);
test-split ids pinned in `ml_test_set_session_ids.json`
(`train_test_split`, `test_size=0.2`, `stratify=label`, `random_state=42`).

| Metric | Value | Confusion | | |
|---|---|---|---|---|
| precision | 1.0 | TP | 78 | FP | 0 |
| recall | 0.975 | FN | 2 | TN | 320 |
| f1 | 0.9873 | | | | |
| false positive rate | 0.0 | | | | |

Breakdown by attack type: credential_stuffing 22/22, device_takeover 23/23,
impossible_travel 13/15 (2 FN), token_replay 20/20 detected; 320/320 clean
sessions correctly not flagged. Recorded library versions: scikit-learn 1.9.0,
Python 3.13.7 (offline evaluation on the host).

### Live ML signal (step 10)

The offline-trained Isolation Forest is now served live alongside the baseline
(never fused; both signals reported separately).

- Artifact: `ml_model/isolation_forest_v1.joblib`, built by
  `train_ml_model.py` on the host with the validated Phase 5 environment
  (Python 3.13, scikit-learn 1.9.0, joblib 1.6.0). The script reproduces the
  exact Phase 5 split/fit, then reloads the SAVED artifact and re-evaluates the
  held-out test split, refusing to continue unless it reproduces the frozen
  `ml_results.json` exactly (metrics, confusion matrix, attack-type breakdown,
  class distribution). Regenerate with `python train_ml_model.py`.
  Artifact SHA-256: `DFE0F0CB56366A3E0CE7AFE7E6AA16F8B6E00E31E9C38AB0FC9BA889EB795952`.
- Serving: loaded ONCE per process (FastAPI lifespan; lock-guarded lazy
  first-use fallback for test clients) — never per request, never retrained
  live. The container pins scikit-learn 1.9.0 + joblib 1.6.0
  (`requirements.txt`); `tests/test_ml_signal.py` re-verifies the artifact
  inside the container (byte hash + version + 25 deterministic golden rows).
- Feature contract: rows built by the frozen `feature_row` helper — identical
  order/encoding to Phase 5 by construction.
- Outputs: `ml_anomaly_flag` (Phase 5 convention: predict -1 → flagged) and
  `ml_decision_score` (raw, uncalibrated `decision_function`; higher = more
  normal, negative = anomalous; not a probability). No 0–100 conversion, no
  fusion, and no offline metric is claimed for the live system.
- Measured live characteristic: first-ever sessions report geo velocity 0.0
  (`no_history`), below the offline normal range (0.98–898.94 km/h), so the
  model flags them as a *marginal* anomaly (decision score just below zero,
  e.g. −0.0127) while attack-like patterns score well below −0.2 (measured:
  impossible travel −0.266, device change −0.223, token reuse −0.236, burst
  −0.257, combined −0.282; clean returning session +0.006). This is measured,
  documented behavior of the validated artifact.
- ML fields are response-only; `contributing_signals` and all existing
  response fields are unchanged (Maheen's contract intact).

## 5. Frozen files (do not edit)

These files are byte-frozen — the evaluated experiments must be reproducible
from exactly these artifacts. SHA-256:

| File | SHA-256 |
|---|---|
| `app/services/baseline_scorer.py` | `7FA30FDD3E04A01CF03B8A5A44ED6BB76BEEC50807F77CBE9D3A4BB97608F4DD` |
| `app/services/session_store.py` | `37F2F9D802085694CA2E47F317910D7203440B0E8FA8C2A2FA5F68E7FA4565C7` |
| `app/services/isolation_forest_scorer.py` | `4BD5CDE4B861EEB56102F3570064427AA6DAD19A7886D9F2A926D1060EA3AB60` |
| `evaluate_baseline.py` | `20C893ABCD42A1E7605AA42E6C0B0984781347DFE659EF262AE1C5CB37A451ED` |
| `baseline_results.json` | `EAA77AE850DDAD7B4B24A33D0DF04FDE6216A4C224F68EFBDD1D83390210EACE` |
| `ml_results.json` | `3EFD4A691C001C781223A5DF7AE1ACE753BE2FD6295E270A9A3B9638311E33E9` |
| `ml_test_set_session_ids.json` | `EB1FCA056B9D421C204C90BAA71989FEF7629909D5632A2222C5940CCAC8299B` |

## 6. Issues fixed in step 9

1. **First-write race in user find-or-create.** Concurrent first-ever requests
   for the same user could both pass the `SELECT` and race on the `users.user_id`
   unique constraint; the losing request raised `IntegrityError` → 503
   (reproduced: 1–6 of 10 simultaneous requests failing). Fix: `INSERT ...
   ON CONFLICT DO NOTHING` scoped to the `user_id` index + re-select (no new
   race, no swallowed unrelated errors). Verified: 5 consecutive 10-request
   concurrency runs, all 200 / single user row.
2. **SQL/constraint leak in 503 responses.** The 503 handler embedded
   `str(exc)`, exposing SQL statements, table/column/constraint names and
   driver details. Fix: fixed generic client body ("Scoring service temporarily
   unavailable. Please try again later.") for **all** 503 causes; the full
   exception is logged server-side only. Covered by a test that forces a real
   `IntegrityError` and asserts the body leaks nothing.
3. **Missing input length limits.** Empty strings and oversized values were
   accepted (200) and persisted. Fix: schema-level `min_length=1` +
   `max_length` caps (`user_id` 255, `ip_address` 45, `device_fingerprint` 255)
   → 422, nothing persisted. Malformed-but-non-empty IPs still score 200 as
   `invalid_ip` (unchanged). No DB migration — enforcement is schema-level only.

Suite after step 9: `41 passed, 0 xfailed` (run inside the stack).

## 7. Remaining roadmap

- **CORS** — dev-origin middleware already present (`config.cors_origins`);
  extend/review origins for the dashboard integration and production.
- **IEEE-CIS validation** — evaluate the pipeline/scorer on the public
  IEEE-CIS fraud dataset (beyond the controlled synthetic set) for external
  validity.
- **Burp Suite tests** — API security testing (request tampering, injection,
  auth bypass) against the running stack.
- **Methodology / results write-up** — FYP report chapter covering design,
  experiments, and results.
