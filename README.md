# ATE (AdaptiveTrust Engine) — Backend Skeleton

Session-security backend for fintech applications. This phase provides the
FastAPI skeleton plus PostgreSQL/Redis infrastructure only. The session risk
engine (device fingerprinting, geo-velocity, token monitoring, behavioural
analysis) will be implemented later in `app/services/`.

## Prerequisites

- Docker with Docker Compose

## Start

```bash
cp .env.example .env   # optional — Compose works with built-in defaults
docker compose up --build
```

Then verify: `GET http://localhost:8000/health` returns `{"status": "ok"}`.

## Run tests

```bash
docker compose exec api python -m pytest -q
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Health check — returns `{"status": "ok"}` |
| POST | /session/score | Accepts `user_id`, `ip_address`, `device_fingerprint`; currently returns a hardcoded stub `{"risk_score": 0, "risk_tier": "low"}` — no risk logic yet |
