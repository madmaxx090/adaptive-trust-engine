# ATE Live API Security Test Results

- **Run ID:** `88c9b8d7`
- **Timestamp (UTC):** 2026-09-21T21:02:43.022886+00:00
- **Target:** `http://localhost:8008` (live Docker Compose stack, real HTTP)
- **Client:** httpx 0.28.1 on Python 3.13.7 (Windows-11-10.0.26200-SP0)
- **Pre-flight:** `GET /health` -> 200 `{"status": "ok"}`

## Summary

| Metric | Value |
|---|---|
| Tests executed | 12 |
| PASS | 10 |
| FINDING | 1 |
| FAIL | 1 |
| Overall | **REVIEW_REQUIRED** |

## Input fuzzing

| ID | Test | Expected | Actual | Verdict |
|---|---|---|---|---|
| A1 | malformed_json | 422 (body rejected as invalid JSON) | status=422 (invalid JSON rejected) | **PASS** |
| A2 | sqli_user_id | 200; GET /sessions/{id} returns user_id byte-identical (literal data, not executed) | status=200; literal round-trip OK | **PASS** |
| A3 | xss_device_fingerprint | 200; device_fingerprint stored/returned as a literal string (JSON response, never HTML) | status=200; literal round-trip OK (application/json) | **PASS** |
| A4 | oversized_user_id | 422 (max_length=255 enforced; nothing persisted) | status=422 (length limit held) | **PASS** |
| A5 | null_byte_user_id | Graceful handling: 422, or sanitized 503 (exact generic body), or 200; never a 500 and never leaked internals | status=500 (expected 422/503/200 without leaks) | **FINDING** |
| A6 | wrong_type_user_id | 422 (Pydantic strict str; no int coercion) | no response (RemoteProtocolError('Server disconnected without sending a response.')) | **FAIL** |
| A7 | empty_body | 422 (three required fields missing) | status=422 (missing fields) | **PASS** |
| A8 | malformed_ip_address | 200 + geo_location_status="invalid_ip" (never 422/500) | status=200, geo_location_status="invalid_ip" | **PASS** |

## HTTP method fuzzing

| ID | Test | Expected | Actual | Verdict |
|---|---|---|---|---|
| B1 | delete_session | 405 Method Not Allowed + Allow header containing GET | status=405, Allow=GET | **PASS** |
| B2 | put_session_score | 405 Method Not Allowed + Allow header containing POST | status=405, Allow=POST | **PASS** |
| B3 | patch_sessions | 405 Method Not Allowed + Allow header containing GET | status=405, Allow=GET | **PASS** |
| B4 | options_cors_preflight | 200 CORS preflight (access-control-allow-methods present, origin http://localhost:3000 echoed) - not an error | status=200, allow-methods='DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT', allow-origin='http://localhost:3000' | **PASS** |

## Burst / concurrency probe (observational)

| Metric | Value |
|---|---|
| Requests fired | 30 |
| Succeeded (200) | 30 |
| HTTP 500 | 0 |
| Client-side errors | 0 |
| Status distribution | {"200": 30} |
| Distinct sessions created | 30 |
| Total wall time | 1.069 s |
| Latency min / median / max | 781.2 / 1033.5 / 1055.0 ms |

This probe is informational only (no pass/fail is defined): it documents actual behavior under burst load against the live system.

## Findings

### A5 null_byte_user_id - FINDING

- Expected: Graceful handling: 422, or sanitized 503 (exact generic body), or 200; never a 500 and never leaked internals
- Actual: status=500 (expected 422/503/200 without leaks)
- `POST /session/score` -> 500: `Internal Server Error`

### A6 wrong_type_user_id - FAIL

- Expected: 422 (Pydantic strict str; no int coercion)
- Actual: no response (RemoteProtocolError('Server disconnected without sending a response.'))
- `POST /session/score` -> no response: `null`

## Probe-created state (traceability)

- User ids created: `1' OR '1'='1`, `sec-badip-88c9b8d7`, `sec-burst-88c9b8d7`, `sec-xss-88c9b8d7`
- Session ids created: 34 (`04de346f-68a2-4c68-b1e1-1cb066c51b95`, `07e2c2ce-52e1-4c58-86f9-42e2c2996b65`, `101a5a97-5d7e-4ccb-8012-7ad5fe133f8c`, `1b619031-2aab-47f9-8451-ccd67f23d214`, `22804dda-6185-4ebe-b1d4-c8541a65e5c4`, `26136872-f369-4904-90e1-f1572eedddcc`, `29c7446f-bbaf-4234-add7-c10f3875a912`, `313f2bfd-4739-4b4a-8d98-8761ab5889ec`, `38aa9eda-c221-4fc8-bc1f-e371e1c805d9`, `3906db81-5f2b-42d7-b1cb-34f626cf92ca`, `49ada574-2dd2-415d-8b2c-a0b6c9126809`, `5d33dc2e-b669-4747-864c-57f652b1ff40`, `681f0013-3e03-43fe-80ee-bffd6576841c`, `68f57e79-0deb-4bcd-b1c6-d0ccf3e1f3ff`, `69fd96bd-bd0a-4dc4-9b21-06fcfe5900e1`, `6c8bac67-d6b6-44f6-8bbd-ead8b0cce90a`, `75774aa4-c8bb-4a64-b872-a2d77311eaff`, `83e184d2-59f0-43a3-a88a-9bee0bdbae21`, `8ae6c08d-e399-45af-a5d3-657df2006643`, `9619509b-560d-4fc4-bb62-db7b06d59cac`, `9a41a937-b4dd-48d7-ae69-69b1f5efeb1b`, `a09b9948-b6e7-492a-80a4-e12a129f0761`, `ab5bef7f-b167-42e3-a1e6-36eb971670c1`, `afb1d635-ea8e-4afc-afb3-933edf1e5eac`, `bf3c02b4-912f-425f-84f9-6b122cec46e3`, `d11c1a13-2bbd-4d53-800a-c29ebfcb8f80`, `d4706a61-7b25-405d-902c-d917922ae084`, `d5d31fa1-eed5-422a-8d89-9dc468b6340c`, `d8ff8802-7af6-46ac-a034-46dc55bea763`, `e425bca3-f002-4924-a15b-f1a75cb463a0`, `e6bd0f6d-742a-4383-a909-00e76a79fef4`, `e6f5a107-604f-41f5-98bc-7ffcf76178fa`, `fafff3ca-181a-4060-8992-2d565a9bda28`, `fcacb401-0d20-411b-bb3e-ba22de64df97`)

## Notes & limitations

- Black-box, host-side probe over real HTTP only: no application imports, no TestClient, no direct database/Redis access. DB-level "no writes" assertions remain covered by the in-container pytest suites (tests/).
- Complements Burp Suite's manual testing; this script is the reproducible, committed artifact of the same security checks.
- The burst probe observes behavior under concurrent load and does not define pass/fail, per the agreed testing plan.
- Probe-created rows are intentionally NOT cleaned up (requests-only scope). Use the ids above for any manual review or cleanup.
