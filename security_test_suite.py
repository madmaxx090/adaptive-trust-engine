"""ATE live API security probe - black-box malformed-input / method / burst tests.

Attacker-perspective probe of the RUNNING stack at http://localhost:8008,
using real HTTP requests only. Companion artifact to the in-container pytest
suites (tests/) and to Burp Suite's manual testing; nothing is imported from
the application package, and no in-process TestClient is used.

What this script does:
- input fuzzing of POST /session/score (malformed JSON, SQLi-style user_id,
  XSS-style device_fingerprint, oversized user_id, null byte, wrong type,
  empty body, malformed ip_address);
- HTTP method fuzzing (DELETE /sessions/{id}, PUT /session/score,
  PATCH /sessions, CORS preflight OPTIONS /session/score);
- a 30-request burst probe against POST /session/score (observation only).

Reporting contract: every test prints one console line and lands in
security_test_results.json (structured) and security_test_results.md
(human-readable, for the research paper). A deviation from the documented
expectation is reported as a FINDING - nothing is ever "fixed" here.

Probe-created state: the 200 paths create real rows in the dev database
(users / sessions / risk events and Redis keys). This script is
requests-only and does NOT clean up; every created id is listed in both
output files for traceability.

Usage (host):  python security_test_suite.py
Exit code:     0 = every test PASS; 1 = any FINDING or FAIL (the full suite
               still runs to completion before exiting).
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import BrokenBarrierError

import httpx

# ---------------------------------------------------------------------------
# Configuration (fixed before the run - do not tune)
# ---------------------------------------------------------------------------
BASE_URL = "http://localhost:8008"   # host port of the compose `api` service
REQUEST_TIMEOUT_SECONDS = 10.0
BURST_REQUEST_COUNT = 30
BURST_TIMEOUT_SECONDS = 60.0
JSON_BODY_CAP = 4000                 # defensive cap for pathological bodies

RUN_ID = uuid.uuid4().hex[:8]        # per-run tag for unique probe user ids

BASE_DIR = Path(__file__).resolve().parent
RESULTS_JSON_PATH = BASE_DIR / "security_test_results.json"
RESULTS_MD_PATH = BASE_DIR / "security_test_results.md"

# Exact fixed 503 body for infrastructure failures, duplicated from
# app/api/session.py (SERVICE_UNAVAILABLE_DETAIL) because this probe must not
# import the application package.
GENERIC_503_DETAIL = (
    "Scoring service temporarily unavailable. Please try again later."
)

# Substrings that must never appear in a client-facing response body
# (server-side logs are outside this black-box probe's visibility).
LEAK_MARKERS = (
    "Traceback",
    "[SQL",
    "UniqueViolation",
    "psycopg2",
    "StatementError",
    "INSERT INTO",
    "users_user_id_key",
    "sessions_session_id_key",
    "NUL (0x00)",
)

# Allowed CORS origin from app/core/config.py defaults (no .env override in
# this stack), used to exercise a genuine preflight request.
CORS_ALLOWED_ORIGIN = "http://localhost:3000"

# Response headers worth recording verbatim (method / CORS / content checks).
RECORDED_HEADERS = (
    "allow",
    "content-type",
    "access-control-allow-methods",
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "vary",
)


# ---------------------------------------------------------------------------
# Request plumbing
# ---------------------------------------------------------------------------
@dataclass
class Step:
    """One real HTTP request/response pair (a transport error is captured)."""

    method: str
    path: str
    status_code: int | None
    body: object
    headers: dict[str, str]
    duration_ms: float
    error: str | None = None


@dataclass
class TestResult:
    test_id: str
    section: str
    name: str
    expected: str
    verdict: str                      # PASS | FINDING | FAIL
    actual: str                       # short human summary for console/report
    request: str = ""
    notes: str = ""
    steps: list[Step] = field(default_factory=list)
    created_user_ids: list[str] = field(default_factory=list)
    created_session_ids: list[str] = field(default_factory=list)


def send(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json_body: object | None = None,
    content: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> Step:
    """Send one real HTTP request; a transport error is captured, never raised."""
    kwargs: dict[str, object] = {}
    if json_body is not None:
        kwargs["json"] = json_body
    if content is not None:
        kwargs["content"] = content
    if headers is not None:
        kwargs["headers"] = headers

    start = time.perf_counter()
    try:
        response = client.request(method, path, timeout=timeout, **kwargs)
    except Exception as exc:  # probe failure, distinct from a response deviation
        return Step(
            method, path, None, None, {},
            (time.perf_counter() - start) * 1000.0, error=repr(exc),
        )
    return Step(
        method=method,
        path=path,
        status_code=response.status_code,
        body=_parse_body(response),
        headers={
            name: response.headers[name]
            for name in RECORDED_HEADERS
            if name in response.headers
        },
        duration_ms=(time.perf_counter() - start) * 1000.0,
    )


def _parse_body(response: httpx.Response) -> object:
    try:
        parsed: object = response.json()
    except Exception:
        parsed = response.text
    return _capped(parsed)


def _capped(value: object) -> object:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= JSON_BODY_CAP:
        return value
    return text[:JSON_BODY_CAP] + f"... [truncated; {len(text)} characters total]"


def _leaked_markers(body: object) -> list[str]:
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return [marker for marker in LEAK_MARKERS if marker in text]


def _geo_status_of(step: Step) -> object:
    body = step.body
    if isinstance(body, dict) and isinstance(body.get("contributing_signals"), dict):
        return body["contributing_signals"].get("geo_location_status")
    return None


def _simple_result(
    test_id: str,
    section: str,
    name: str,
    request: str,
    expected: str,
    step: Step,
    expected_status: int,
    pass_label: str,
    notes: str = "",
) -> TestResult:
    """Shared verdict logic for single-request tests with one expected status."""
    if step.error is not None:
        return TestResult(
            test_id, section, name, expected, "FAIL",
            f"no response ({step.error})", request, notes, [step],
        )
    if step.status_code == expected_status:
        return TestResult(
            test_id, section, name, expected, "PASS",
            f"status={step.status_code} {pass_label}".rstrip(), request, notes, [step],
        )
    return TestResult(
        test_id, section, name, expected, "FINDING",
        f"status={step.status_code} (expected {expected_status})", request, notes,
        [step],
    )


def preflight(client: httpx.Client) -> dict:
    """Abort loudly when the live stack is not up: there is nothing to probe."""
    try:
        response = client.get("/health", timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        raise SystemExit(
            f"ERROR: live API unreachable at {BASE_URL} ({exc!r}); start the "
            "stack (docker compose up -d) and re-run."
        ) from exc
    try:
        body: object = response.json()
    except Exception:
        body = response.text
    if response.status_code != 200 or not (
        isinstance(body, dict) and body.get("status") == "ok"
    ):
        raise SystemExit(
            f'ERROR: pre-flight GET /health returned {response.status_code} '
            f'{body!r}; expected 200 {{"status": "ok"}}.'
        )
    return {"status_code": response.status_code, "body": body}


# ---------------------------------------------------------------------------
# A. Input fuzzing - POST /session/score
# ---------------------------------------------------------------------------
def test_a1_malformed_json(client: httpx.Client) -> TestResult:
    step = send(
        client, "POST", "/session/score",
        content='{"user_id": "x", "ip_address":',
        headers={"Content-Type": "application/json"},
    )
    return _simple_result(
        "A1", "Input fuzzing", "malformed_json",
        "POST /session/score, truncated JSON body",
        "422 (body rejected as invalid JSON)",
        step, 422, "(invalid JSON rejected)",
    )


def test_a2_sqli_user_id(client: httpx.Client, captured: dict) -> TestResult:
    sql_literal = "1' OR '1'='1"
    request = "POST /session/score, user_id = SQLi literal"
    expected = (
        "200; GET /sessions/{id} returns user_id byte-identical "
        "(literal data, not executed)"
    )
    post = send(
        client, "POST", "/session/score",
        json_body={
            "user_id": sql_literal,
            "ip_address": "192.168.1.10",
            "device_fingerprint": "device-A",
        },
    )
    steps = [post]
    if post.error is not None:
        return TestResult(
            "A2", "Input fuzzing", "sqli_user_id", expected, "FAIL",
            f"no response ({post.error})", request, steps=steps,
        )
    if post.status_code != 200:
        return TestResult(
            "A2", "Input fuzzing", "sqli_user_id", expected, "FINDING",
            f"status={post.status_code} (expected 200)", request, steps=steps,
        )
    session_id = post.body.get("session_id") if isinstance(post.body, dict) else None
    if session_id and captured.get("session_id") is None:
        captured["session_id"] = session_id
    detail = send(client, "GET", f"/sessions/{session_id}")
    steps.append(detail)
    literal_round_trip = (
        isinstance(detail.body, dict) and detail.body.get("user_id") == sql_literal
    )
    if literal_round_trip:
        verdict, actual = "PASS", "status=200; literal round-trip OK"
        notes = (
            "SQL-metacharacter string stored and returned verbatim as data "
            "(parameterized ORM access; no SQL executed)."
        )
    else:
        verdict = "FINDING"
        actual = "GET /sessions/{id} did not reproduce the literal user_id"
        notes = f"detail response: {detail.body!r}"
    return TestResult(
        "A2", "Input fuzzing", "sqli_user_id", expected, verdict, actual,
        request, notes, steps, [sql_literal],
        [session_id] if session_id else [],
    )


def test_a3_xss_device_fingerprint(client: httpx.Client, captured: dict) -> TestResult:
    xss_literal = "<script>alert(1)</script>"
    user = f"sec-xss-{RUN_ID}"
    request = "POST /session/score, device_fingerprint = XSS literal"
    expected = (
        "200; device_fingerprint stored/returned as a literal string "
        "(JSON response, never HTML)"
    )
    post = send(
        client, "POST", "/session/score",
        json_body={
            "user_id": user,
            "ip_address": "192.168.1.10",
            "device_fingerprint": xss_literal,
        },
    )
    steps = [post]
    if post.error is not None:
        return TestResult(
            "A3", "Input fuzzing", "xss_device_fingerprint", expected, "FAIL",
            f"no response ({post.error})", request, steps=steps,
        )
    if post.status_code != 200:
        return TestResult(
            "A3", "Input fuzzing", "xss_device_fingerprint", expected, "FINDING",
            f"status={post.status_code} (expected 200)", request, steps=steps,
        )
    session_id = post.body.get("session_id") if isinstance(post.body, dict) else None
    if session_id and captured.get("session_id") is None:
        captured["session_id"] = session_id
    detail = send(client, "GET", f"/sessions/{session_id}")
    steps.append(detail)
    json_content_type = (
        post.headers.get("content-type", "").startswith("application/json")
        and detail.headers.get("content-type", "").startswith("application/json")
    )
    literal_round_trip = (
        isinstance(detail.body, dict)
        and detail.body.get("device_fingerprint") == xss_literal
    )
    if literal_round_trip and json_content_type:
        verdict = "PASS"
        actual = "status=200; literal round-trip OK (application/json)"
        notes = (
            "Markup stored and returned verbatim as a JSON string value; the "
            "API response is never rendered as HTML."
        )
    else:
        verdict = "FINDING"
        actual = "device_fingerprint literal/content-type check failed"
        notes = (
            f"literal_round_trip={literal_round_trip}, "
            f"json_content_type={json_content_type}"
        )
    return TestResult(
        "A3", "Input fuzzing", "xss_device_fingerprint", expected, verdict,
        actual, request, notes, steps, [user],
        [session_id] if session_id else [],
    )


def test_a4_oversized_user_id(client: httpx.Client) -> TestResult:
    step = send(
        client, "POST", "/session/score",
        json_body={
            "user_id": "u" * 10_000,
            "ip_address": "192.168.1.10",
            "device_fingerprint": "device-A",
        },
    )
    return _simple_result(
        "A4", "Input fuzzing", "oversized_user_id",
        "POST /session/score, user_id = 10,000 chars",
        "422 (max_length=255 enforced; nothing persisted)",
        step, 422, "(length limit held)",
        notes="Phase 9 input length limit: user_id max_length=255 (schema-level).",
    )


def test_a5_null_byte_user_id(client: httpx.Client, captured: dict) -> TestResult:
    null_user = f"sec-null-{RUN_ID}-\u0000-probe"
    request = "POST /session/score, user_id contains U+0000"
    expected = (
        "Graceful handling: 422, or sanitized 503 (exact generic body), or "
        "200; never a 500 and never leaked internals"
    )
    step = send(
        client, "POST", "/session/score",
        json_body={
            "user_id": null_user,
            "ip_address": "192.168.1.10",
            "device_fingerprint": "device-A",
        },
    )
    steps = [step]
    if step.error is not None:
        return TestResult(
            "A5", "Input fuzzing", "null_byte_user_id", expected, "FAIL",
            f"no response ({step.error})", request, steps=steps,
        )
    leaked = _leaked_markers(step.body)
    if leaked:
        return TestResult(
            "A5", "Input fuzzing", "null_byte_user_id", expected, "FINDING",
            f"status={step.status_code}; response body leaked markers {leaked}",
            request, "Client-facing body contains internal-detail markers.",
            steps,
        )
    if step.status_code == 422:
        return TestResult(
            "A5", "Input fuzzing", "null_byte_user_id", expected, "PASS",
            "status=422 (rejected before persistence)", request, steps=steps,
        )
    if step.status_code == 503 and step.body == {"detail": GENERIC_503_DETAIL}:
        return TestResult(
            "A5", "Input fuzzing", "null_byte_user_id", expected, "PASS",
            "status=503, sanitized generic body (no crash, no internals)",
            request,
            "Null byte is client input but was mapped onto the sanitized "
            "infrastructure-failure path - semantics note for review, not a "
            "security finding (no crash, no leaked details).",
            steps,
        )
    if step.status_code == 200:
        session_id = step.body.get("session_id") if isinstance(step.body, dict) else None
        if session_id and captured.get("session_id") is None:
            captured["session_id"] = session_id
        return TestResult(
            "A5", "Input fuzzing", "null_byte_user_id", expected, "PASS",
            "status=200 (accepted; request completed without crash)", request,
            "NUL was accepted end-to-end; review what was persisted for this "
            "user_id if further scrutiny is wanted.",
            steps, [null_user], [session_id] if session_id else [],
        )
    return TestResult(
        "A5", "Input fuzzing", "null_byte_user_id", expected, "FINDING",
        f"status={step.status_code} (expected 422/503/200 without leaks)",
        request, steps=steps,
    )


def test_a6_wrong_type_user_id(client: httpx.Client) -> TestResult:
    step = send(
        client, "POST", "/session/score",
        json_body={
            "user_id": 123,
            "ip_address": "192.168.1.10",
            "device_fingerprint": "device-A",
        },
    )
    return _simple_result(
        "A6", "Input fuzzing", "wrong_type_user_id",
        "POST /session/score, user_id = 123 (integer)",
        "422 (Pydantic strict str; no int coercion)",
        step, 422, "(type rejection)",
    )


def test_a7_empty_body(client: httpx.Client) -> TestResult:
    step = send(client, "POST", "/session/score", json_body={})
    return _simple_result(
        "A7", "Input fuzzing", "empty_body",
        "POST /session/score, body = {}",
        "422 (three required fields missing)",
        step, 422, "(missing fields)",
    )


def test_a8_malformed_ip(client: httpx.Client, captured: dict) -> TestResult:
    user = f"sec-badip-{RUN_ID}"
    request = "POST /session/score, ip_address = not-an-ip (2-step)"
    expected = '200 + geo_location_status="invalid_ip" (never 422/500)'
    step1 = send(
        client, "POST", "/session/score",
        json_body={
            "user_id": user,
            "ip_address": "192.168.1.10",
            "device_fingerprint": "device-A",
        },
    )
    step2 = send(
        client, "POST", "/session/score",
        json_body={
            "user_id": user,
            "ip_address": "not-an-ip",
            "device_fingerprint": "device-A",
        },
    )
    steps = [step1, step2]
    notes = (
        'Two-step by design: a fresh user short-circuits to "no_history" '
        "before IP validation, so step 1 establishes the prior session "
        "(documented precedence)."
    )
    created_sessions = [
        body.get("session_id")
        for body in (step1.body, step2.body)
        if isinstance(body, dict) and body.get("session_id")
    ]
    if step1.error is not None or step2.error is not None:
        return TestResult(
            "A8", "Input fuzzing", "malformed_ip_address", expected, "FAIL",
            f"no response ({step1.error or step2.error})", request, notes,
            steps, [user], created_sessions,
        )
    if step1.status_code != 200:
        return TestResult(
            "A8", "Input fuzzing", "malformed_ip_address", expected, "FINDING",
            f"step1 status={step1.status_code} (expected 200)", request, notes,
            steps, [user], created_sessions,
        )
    geo_status = _geo_status_of(step2)
    if step2.status_code == 200 and geo_status == "invalid_ip":
        return TestResult(
            "A8", "Input fuzzing", "malformed_ip_address", expected, "PASS",
            'status=200, geo_location_status="invalid_ip"', request, notes,
            steps, [user], created_sessions,
        )
    return TestResult(
        "A8", "Input fuzzing", "malformed_ip_address", expected, "FINDING",
        f"step2 status={step2.status_code}, "
        f"geo_location_status={geo_status!r} (expected 200/invalid_ip)",
        request, notes, steps, [user], created_sessions,
    )


# ---------------------------------------------------------------------------
# B. HTTP method fuzzing
# ---------------------------------------------------------------------------
def _method_result(
    test_id: str, name: str, request: str, step: Step, expected_allow: str
) -> TestResult:
    expected = f"405 Method Not Allowed + Allow header containing {expected_allow}"
    if step.error is not None:
        return TestResult(
            test_id, "HTTP method fuzzing", name, expected, "FAIL",
            f"no response ({step.error})", request, steps=[step],
        )
    allow = step.headers.get("allow", "")
    if step.status_code == 405 and expected_allow in allow:
        return TestResult(
            test_id, "HTTP method fuzzing", name, expected, "PASS",
            f"status=405, Allow={allow}", request, steps=[step],
        )
    return TestResult(
        test_id, "HTTP method fuzzing", name, expected, "FINDING",
        f"status={step.status_code}, Allow={allow!r} "
        f"(expected 405 + {expected_allow})",
        request, steps=[step],
    )


def test_b1_delete_session(client: httpx.Client, captured: dict) -> TestResult:
    real_session = captured.get("session_id")
    session_id = real_session or str(uuid.uuid4())
    used = (
        "real session id captured earlier in this run"
        if real_session
        else "random session id (no earlier 200 captured)"
    )
    step = send(client, "DELETE", f"/sessions/{session_id}")
    result = _method_result(
        "B1", "delete_session", f"DELETE /sessions/{{id}} ({used})", step, "GET"
    )
    return result


def test_b2_put_score(client: httpx.Client) -> TestResult:
    step = send(
        client, "PUT", "/session/score",
        json_body={
            "user_id": f"sec-method-{RUN_ID}",
            "ip_address": "192.168.1.10",
            "device_fingerprint": "device-A",
        },
    )
    return _method_result("B2", "put_session_score", "PUT /session/score", step, "POST")


def test_b3_patch_sessions(client: httpx.Client) -> TestResult:
    step = send(client, "PATCH", "/sessions")
    return _method_result("B3", "patch_sessions", "PATCH /sessions", step, "GET")


def test_b4_options_preflight(client: httpx.Client) -> TestResult:
    request = (
        f"OPTIONS /session/score (Origin: {CORS_ALLOWED_ORIGIN}, "
        "Access-Control-Request-Method: POST)"
    )
    expected = (
        f"200 CORS preflight (access-control-allow-methods present, origin "
        f"{CORS_ALLOWED_ORIGIN} echoed) - not an error"
    )
    step = send(
        client, "OPTIONS", "/session/score",
        headers={
            "Origin": CORS_ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    if step.error is not None:
        return TestResult(
            "B4", "HTTP method fuzzing", "options_cors_preflight", expected,
            "FAIL", f"no response ({step.error})", request, steps=[step],
        )
    allow_methods = step.headers.get("access-control-allow-methods", "")
    allow_origin = step.headers.get("access-control-allow-origin", "")
    if step.status_code == 200 and allow_methods and allow_origin == CORS_ALLOWED_ORIGIN:
        return TestResult(
            "B4", "HTTP method fuzzing", "options_cors_preflight", expected,
            "PASS",
            f"status=200, allow-methods={allow_methods!r}, "
            f"allow-origin={allow_origin!r}",
            request,
            "Standard preflight answered by the CORS middleware.",
            [step],
        )
    return TestResult(
        "B4", "HTTP method fuzzing", "options_cors_preflight", expected,
        "FINDING",
        f"status={step.status_code}, allow-methods={allow_methods!r}, "
        f"allow-origin={allow_origin!r}",
        request, steps=[step],
    )


# ---------------------------------------------------------------------------
# C. Burst / concurrency probe (observational; no pass/fail)
# ---------------------------------------------------------------------------
def run_burst_probe(client: httpx.Client) -> dict:
    """30 simultaneous real-HTTP requests for one fresh user (report only)."""
    user = f"sec-burst-{RUN_ID}"
    payload = {
        "user_id": user,
        "ip_address": "192.168.1.10",
        "device_fingerprint": "device-burst",
    }
    barrier = threading.Barrier(BURST_REQUEST_COUNT, timeout=BURST_TIMEOUT_SECONDS)

    def one(_index: int) -> dict:
        try:
            barrier.wait()
        except BrokenBarrierError:
            return {
                "status_code": None, "session_id": None,
                "error": "barrier timeout", "latency_ms": None,
            }
        start = time.perf_counter()
        try:
            response = client.post(
                "/session/score", json=payload, timeout=BURST_TIMEOUT_SECONDS
            )
            latency_ms = (time.perf_counter() - start) * 1000.0
            session_id = None
            if response.status_code == 200:
                try:
                    session_id = response.json().get("session_id")
                except Exception:
                    session_id = None
            return {
                "status_code": response.status_code, "session_id": session_id,
                "error": None, "latency_ms": latency_ms,
            }
        except Exception as exc:
            return {
                "status_code": None, "session_id": None, "error": repr(exc),
                "latency_ms": (time.perf_counter() - start) * 1000.0,
            }

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=BURST_REQUEST_COUNT) as pool:
        outcomes = list(pool.map(one, range(BURST_REQUEST_COUNT)))
    total_wall_seconds = time.perf_counter() - wall_start

    statuses = Counter(
        "NO_RESPONSE" if outcome["status_code"] is None else str(outcome["status_code"])
        for outcome in outcomes
    )
    latencies = [
        outcome["latency_ms"]
        for outcome in outcomes
        if outcome["latency_ms"] is not None
    ]
    session_ids = [
        outcome["session_id"] for outcome in outcomes if outcome["session_id"]
    ]
    return {
        "user_id": user,
        "requests_fired": BURST_REQUEST_COUNT,
        "status_distribution": dict(sorted(statuses.items())),
        "succeeded_200": statuses.get("200", 0),
        "http_500_count": statuses.get("500", 0),
        "client_errors": sum(1 for outcome in outcomes if outcome["error"] is not None),
        "distinct_sessions_created": len(set(session_ids)),
        "total_wall_seconds": round(total_wall_seconds, 3),
        "latency_ms": {
            "min": round(min(latencies), 1) if latencies else None,
            "median": round(statistics.median(latencies), 1) if latencies else None,
            "max": round(max(latencies), 1) if latencies else None,
        },
        "created_session_ids": session_ids,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def step_to_dict(step: Step) -> dict:
    data = {
        "method": step.method,
        "path": step.path,
        "status_code": step.status_code,
        "body": step.body,
        "headers": step.headers,
        "duration_ms": round(step.duration_ms, 1),
    }
    if step.error is not None:
        data["error"] = step.error
    return data


def result_to_dict(result: TestResult) -> dict:
    return {
        "id": result.test_id,
        "section": result.section,
        "name": result.name,
        "request": result.request,
        "expected": result.expected,
        "actual": result.actual,
        "verdict": result.verdict,
        "notes": result.notes,
        "duration_ms": round(sum(step.duration_ms for step in result.steps), 1),
        "steps": [step_to_dict(step) for step in result.steps],
        "created_user_ids": result.created_user_ids,
        "created_session_ids": result.created_session_ids,
    }


def write_json(run_meta: dict, results: list[TestResult], burst: dict, summary: dict) -> None:
    payload = {
        "run": run_meta,
        "summary": summary,
        "tests": [result_to_dict(result) for result in results],
        "burst_probe": burst,
        "created_state": {
            "user_ids": sorted(
                {uid for result in results for uid in result.created_user_ids}
                | {burst["user_id"]}
            ),
            "session_ids": sorted(
                {sid for result in results for sid in result.created_session_ids}
                | set(burst["created_session_ids"])
            ),
        },
    }
    RESULTS_JSON_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _md_cell(text: object) -> str:
    return str(text).replace("|", "\\|")


def write_markdown(run_meta: dict, results: list[TestResult], burst: dict, summary: dict) -> None:
    lines: list[str] = []
    lines.append("# ATE Live API Security Test Results")
    lines.append("")
    lines.append(f"- **Run ID:** `{run_meta['run_id']}`")
    lines.append(f"- **Timestamp (UTC):** {run_meta['timestamp_utc']}")
    lines.append(
        f"- **Target:** `{run_meta['target_base_url']}` "
        "(live Docker Compose stack, real HTTP)"
    )
    lines.append(
        f"- **Client:** httpx {run_meta['httpx_version']} on Python "
        f"{run_meta['python_version']} ({run_meta['host_os']})"
    )
    lines.append(
        f"- **Pre-flight:** `GET /health` -> "
        f"{run_meta['health_preflight']['status_code']} "
        f"`{json.dumps(run_meta['health_preflight']['body'])}`"
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Tests executed | {summary['total_tests']} |")
    lines.append(f"| PASS | {summary['passed']} |")
    lines.append(f"| FINDING | {summary['findings']} |")
    lines.append(f"| FAIL | {summary['failed']} |")
    lines.append(f"| Overall | **{summary['overall']}** |")
    lines.append("")
    for section in ("Input fuzzing", "HTTP method fuzzing"):
        lines.append(f"## {section}")
        lines.append("")
        lines.append("| ID | Test | Expected | Actual | Verdict |")
        lines.append("|---|---|---|---|---|")
        for result in results:
            if result.section == section:
                lines.append(
                    f"| {result.test_id} | {_md_cell(result.name)} | "
                    f"{_md_cell(result.expected)} | {_md_cell(result.actual)} | "
                    f"**{result.verdict}** |"
                )
        lines.append("")
    lines.append("## Burst / concurrency probe (observational)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Requests fired | {burst['requests_fired']} |")
    lines.append(f"| Succeeded (200) | {burst['succeeded_200']} |")
    lines.append(f"| HTTP 500 | {burst['http_500_count']} |")
    lines.append(f"| Client-side errors | {burst['client_errors']} |")
    lines.append(
        f"| Status distribution | {_md_cell(json.dumps(burst['status_distribution']))} |"
    )
    lines.append(f"| Distinct sessions created | {burst['distinct_sessions_created']} |")
    lines.append(f"| Total wall time | {burst['total_wall_seconds']} s |")
    lines.append(
        f"| Latency min / median / max | {burst['latency_ms']['min']} / "
        f"{burst['latency_ms']['median']} / {burst['latency_ms']['max']} ms |"
    )
    lines.append("")
    lines.append(
        "This probe is informational only (no pass/fail is defined): it "
        "documents actual behavior under burst load against the live system."
    )
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    deviations = [result for result in results if result.verdict != "PASS"]
    if not deviations:
        lines.append("None - every test matched its documented expectation.")
        lines.append("")
    else:
        for result in deviations:
            lines.append(f"### {result.test_id} {result.name} - {result.verdict}")
            lines.append("")
            lines.append(f"- Expected: {result.expected}")
            lines.append(f"- Actual: {result.actual}")
            if result.notes:
                lines.append(f"- Notes: {result.notes}")
            for step in result.steps:
                status = step.status_code if step.status_code is not None else "no response"
                body_text = (
                    step.body
                    if isinstance(step.body, str)
                    else json.dumps(step.body, ensure_ascii=False)
                )
                lines.append(f"- `{step.method} {step.path}` -> {status}: `{body_text}`")
            lines.append("")
    lines.append("## Probe-created state (traceability)")
    lines.append("")
    created_users = sorted(
        {uid for result in results for uid in result.created_user_ids}
        | {burst["user_id"]}
    )
    created_sessions = sorted(
        {sid for result in results for sid in result.created_session_ids}
        | set(burst["created_session_ids"])
    )
    lines.append(f"- User ids created: {', '.join(f'`{u}`' for u in created_users)}")
    lines.append(
        f"- Session ids created: {len(created_sessions)} "
        f"({', '.join(f'`{s}`' for s in created_sessions) if created_sessions else 'none'})"
    )
    lines.append("")
    lines.append("## Notes & limitations")
    lines.append("")
    lines.append(
        "- Black-box, host-side probe over real HTTP only: no application "
        "imports, no TestClient, no direct database/Redis access. DB-level "
        '"no writes" assertions remain covered by the in-container pytest '
        "suites (tests/)."
    )
    lines.append(
        "- Complements Burp Suite's manual testing; this script is the "
        "reproducible, committed artifact of the same security checks."
    )
    lines.append(
        "- The burst probe observes behavior under concurrent load and does "
        "not define pass/fail, per the agreed testing plan."
    )
    lines.append(
        "- Probe-created rows are intentionally NOT cleaned up (requests-only "
        "scope). Use the ids above for any manual review or cleanup."
    )
    lines.append("")
    RESULTS_MD_PATH.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print("=== ATE live API security probe (black-box, real HTTP) ===")
    print(
        f"Target: {BASE_URL} | Run ID: {RUN_ID} | Python "
        f"{platform.python_version()} | httpx {httpx.__version__}"
    )

    limits = httpx.Limits(max_connections=60, max_keepalive_connections=30)
    with httpx.Client(
        base_url=BASE_URL, timeout=REQUEST_TIMEOUT_SECONDS, limits=limits
    ) as client:
        health = preflight(client)
        print(
            f"Pre-flight: GET /health -> {health['status_code']} "
            f"{json.dumps(health['body'])}  OK"
        )
        print()

        captured: dict[str, str | None] = {"session_id": None}
        results = [
            test_a1_malformed_json(client),
            test_a2_sqli_user_id(client, captured),
            test_a3_xss_device_fingerprint(client, captured),
            test_a4_oversized_user_id(client),
            test_a5_null_byte_user_id(client, captured),
            test_a6_wrong_type_user_id(client),
            test_a7_empty_body(client),
            test_a8_malformed_ip(client, captured),
            test_b1_delete_session(client, captured),
            test_b2_put_score(client),
            test_b3_patch_sessions(client),
            test_b4_options_preflight(client),
        ]

        for result in results:
            duration_ms = sum(step.duration_ms for step in result.steps)
            print(
                f"[{result.verdict}] {result.test_id} {result.name} | "
                f"expected: {result.expected} | actual: {result.actual} | "
                f"{duration_ms:.0f} ms"
            )

        print()
        print("=== Burst probe (observational, no pass/fail) ===")
        burst = run_burst_probe(client)
        print(
            f"[C1] burst_probe | {burst['requests_fired']} concurrent requests, "
            f"user {burst['user_id']} | statuses: {burst['status_distribution']}, "
            f"500s: {burst['http_500_count']}, client errors: "
            f"{burst['client_errors']} | wall: {burst['total_wall_seconds']} s | "
            f"latency min/median/max: {burst['latency_ms']['min']}/"
            f"{burst['latency_ms']['median']}/{burst['latency_ms']['max']} ms"
        )

        summary = {
            "total_tests": len(results),
            "passed": sum(result.verdict == "PASS" for result in results),
            "findings": sum(result.verdict == "FINDING" for result in results),
            "failed": sum(result.verdict == "FAIL" for result in results),
        }
        summary["overall"] = (
            "PASS"
            if summary["findings"] == 0 and summary["failed"] == 0
            else "REVIEW_REQUIRED"
        )

        run_meta = {
            "run_id": RUN_ID,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "target_base_url": BASE_URL,
            "python_version": platform.python_version(),
            "httpx_version": httpx.__version__,
            "host_os": platform.platform(),
            "health_preflight": health,
        }

        write_json(run_meta, results, burst, summary)
        write_markdown(run_meta, results, burst, summary)

        print()
        print(
            f"=== Summary: {summary['total_tests']} tests - "
            f"{summary['passed']} PASS, {summary['findings']} FINDING, "
            f"{summary['failed']} FAIL | overall: {summary['overall']} ==="
        )
        print(
            f"Results written: {RESULTS_JSON_PATH.name}, {RESULTS_MD_PATH.name}"
        )

    return 0 if summary["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
