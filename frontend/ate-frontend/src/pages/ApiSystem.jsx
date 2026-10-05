import { useMemo, useState } from "react";
import {
  getHealth,
  getSessionDetail,
  getSessions,
  scoreSession,
} from "../services/api";

export default function ApiSystem({ onToast }) {
  const endpoints = useMemo(() => [
    {
      key: "GET /health",
      method: "GET",
      path: "/health",
      description: "Check whether the backend API is online.",
      example: { status: "ok" },
    },
    {
      key: "GET /sessions",
      method: "GET",
      path: "/sessions",
      description: "Load the session list used by the main dashboard.",
      example: {
        total: 1,
        page: 1,
        limit: 20,
        sessions: [
          {
            session_id: "5f2c9d1a-3b4e-4c7d-8a91-0e6f1b2c3d4e",
            user_id: "user_107",
            risk_score: 88,
            risk_tier: "high",
            device_fingerprint: "ate_demo_browser_fingerprint",
            ip_address: "39.42.10.25",
            timestamp: "2026-09-25T06:28:49Z",
          },
        ],
      },
    },
    {
      key: "GET /sessions/{session_id}",
      method: "GET",
      path: "/sessions/{session_id}",
      description: "Load a full detail response for the most recent real session.",
      example: {
        session_id: "5f2c9d1a-3b4e-4c7d-8a91-0e6f1b2c3d4e",
        user_id: "user_107",
        risk_score: 88,
        risk_tier: "high",
        contributing_signals: {
          geo_velocity_kmh: 933.7,
          geo_location_status: "ok",
          device_mismatch_score: 1.0,
          token_reuse_flag: true,
          login_burst_count: 12,
        },
        device_fingerprint: "ate_demo_browser_fingerprint",
        ip_address: "39.42.10.25",
        timestamp: "2026-09-25T06:28:49Z",
        history: [{ event: "risk_scored", timestamp: "2026-09-25T06:28:49Z" }],
      },
    },
    {
      key: "POST /session/score",
      method: "POST",
      path: "/session/score",
      description: "Score a session using user, IP address and device fingerprint.",
      example: {
        session_id: "5f2c9d1a-3b4e-4c7d-8a91-0e6f1b2c3d4e",
        risk_score: 88,
        risk_tier: "high",
        contributing_signals: {
          geo_velocity_kmh: 933.7,
          geo_location_status: "ok",
          device_mismatch_score: 1.0,
          token_reuse_flag: true,
          login_burst_count: 12,
        },
        ml_anomaly_flag: false,
        ml_decision_score: 0.0721,
      },
    },
  ], []);

  const [selectedKey, setSelectedKey] = useState(endpoints[0].key);
  const [checking, setChecking] = useState(false);
  const [backendStatus, setBackendStatus] = useState("Not checked");
  const [latency, setLatency] = useState("—");
  const [lastChecked, setLastChecked] = useState("—");
  const [result, setResult] = useState(null);
  const [requestError, setRequestError] = useState("");

  const selected = endpoints.find((item) => item.key === selectedKey) || endpoints[0];

  const executeSelected = async () => {
    const started = performance.now();
    setChecking(true);
    setRequestError("");

    try {
      let data;

      if (selected.key === "GET /health") {
        data = await getHealth();
      } else if (selected.key === "GET /sessions") {
        data = await getSessions();
      } else if (selected.key === "GET /sessions/{session_id}") {
        const list = await getSessions();
        if (!list.sessions.length) {
          throw new Error("No sessions available to load a detail for yet");
        }
        data = await getSessionDetail(list.sessions[0].session_id);
      } else {
        data = await scoreSession({
          user_id: "user_107",
          ip_address: "39.42.10.25",
          device_fingerprint: "ate_demo_browser_fingerprint",
        });
      }

      const elapsed = Math.round(performance.now() - started);
      setLatency(`${elapsed} ms`);
      setBackendStatus("Online");
      setLastChecked(new Date().toLocaleTimeString());
      setResult(data);
      onToast?.("API request completed");
    } catch (error) {
      const elapsed = Math.round(performance.now() - started);
      setLatency(`${elapsed} ms`);
      setBackendStatus("Unavailable");
      setLastChecked(new Date().toLocaleTimeString());
      setResult(null);
      setRequestError(error.message || "Backend request failed");
      onToast?.("Backend is not reachable yet", "error");
    } finally {
      setChecking(false);
    }
  };

  return (
    <section className="page-stack api-system-page">
      <div className="health-grid">
        <div className="health-card">
          <span>Frontend</span>
          <strong className="health-good">Online</strong>
          <small>React / Vite application</small>
        </div>
        <div className="health-card">
          <span>Backend API</span>
          <strong className={backendStatus === "Online" ? "health-good" : backendStatus === "Unavailable" ? "health-bad" : ""}>
            {backendStatus}
          </strong>
          <small>http://localhost:8008</small>
        </div>
        <div className="health-card">
          <span>Last latency</span>
          <strong>{latency}</strong>
          <small>Most recent API request</small>
        </div>
        <div className="health-card">
          <span>Last checked</span>
          <strong>{lastChecked}</strong>
          <small>Local browser time</small>
        </div>
      </div>

      <div className="api-system-grid">
        <div className="panel endpoint-list">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">API CONTRACT</p>
              <h2>Endpoints</h2>
              <p>Select an endpoint, then test it against the real backend.</p>
            </div>
          </div>

          {endpoints.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`endpoint-button ${selected.key === item.key ? "active" : ""}`}
              onClick={() => {
                setSelectedKey(item.key);
                setResult(null);
                setRequestError("");
              }}
            >
              <span className={`method method-${item.method.toLowerCase()}`}>{item.method}</span>
              <div>
                <strong>{item.path}</strong>
                <span>{item.description}</span>
              </div>
            </button>
          ))}
        </div>

        <div className="panel api-test-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">{selected.method} REQUEST</p>
              <h2>{selected.path}</h2>
              <p>{selected.description}</p>
            </div>
            <button className="primary-button" type="button" onClick={executeSelected} disabled={checking}>
              {checking ? "Sending…" : "Send request"}
            </button>
          </div>

          {selected.key === "POST /session/score" && (
            <div className="request-preview">
              <div className="code-label">Demo request body</div>
              <pre className="code-block compact-code">{JSON.stringify({
                user_id: "user_107",
                ip_address: "39.42.10.25",
                device_fingerprint: "ate_demo_browser_fingerprint",
              }, null, 2)}</pre>
            </div>
          )}

          {requestError ? (
            <div className="api-error-state">
              <strong>Backend request failed</strong>
              <p>{requestError}</p>
              <small>Check that the backend is reachable at http://localhost:8008, then retry.</small>
            </div>
          ) : (
            <>
              <div className="code-label">{result ? "Live response" : "Expected response example"}</div>
              <pre className="code-block">{JSON.stringify(result || selected.example, null, 2)}</pre>
            </>
          )}
        </div>
      </div>

      <div className="panel integration-strip">
        <div>
          <strong>Why API Explorer and System Health are merged</strong>
          <p>System Health checks API availability; API Explorer tests the actual contract endpoints. They belong together as one backend integration workspace.</p>
        </div>
        <span className="integration-chip">REAL REQUEST READY</span>
      </div>
    </section>
  );
}
