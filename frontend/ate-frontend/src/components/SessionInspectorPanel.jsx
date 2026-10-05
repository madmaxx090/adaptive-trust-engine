import RiskBadge from "./RiskBadge";

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "—";
  return date.toLocaleString([], {
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function SignalBar({ label, value, tone = "blue" }) {
  const percent = Math.max(0, Math.min(100, Math.round((value || 0) * 100)));

  return (
    <div className="analysis-signal-row">
      <div className="analysis-signal-head">
        <span>{label}</span>
        <strong>{Number(value || 0).toFixed(2)}</strong>
      </div>
      <div className="analysis-signal-track">
        <span className={`signal-tone-${tone}`} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function DetailCard({ icon, title, children }) {
  return (
    <section className="session-analysis-card">
      <div className="session-analysis-title">
        <span className="analysis-icon">{icon}</span>
        <h4>{title}</h4>
      </div>
      {children}
    </section>
  );
}

function KeyValue({ label, value, mono = false }) {
  return (
    <div className="analysis-kv">
      <span>{label}</span>
      <strong className={mono ? "mono" : ""}>{value ?? "—"}</strong>
    </div>
  );
}

// session: the row clicked in the list (basic fields only)
// detail: the REAL fetched detail from GET /sessions/{id} (contributing_signals, history)
// loading / error: state of the detail fetch
export default function SessionInspectorPanel({ session, detail, loading, error }) {
  if (!session) {
    return (
      <div className="session-empty-inspector">
        <div className="empty-inspector-icon">✕</div>
        <h3>Select a session</h3>
        <p>Click a session in the list to inspect its behavior and risk signals.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="session-empty-inspector">
        <div className="empty-inspector-icon">…</div>
        <h3>Loading session details...</h3>
      </div>
    );
  }

  if (error) {
    return (
      <div className="session-empty-inspector">
        <div className="empty-inspector-icon">!</div>
        <h3>Unable to load session details</h3>
        <p>{error}</p>
      </div>
    );
  }

  // signals now come from the REAL backend response, real field names
  const signals = detail?.contributing_signals || null;
  const history = detail?.history || [];

  // geo_velocity_kmh is an uncapped real speed value, not a 0-1 score like the
  // old mock's geo_velocity_score -- normalize it against the same 1000 km/h
  // cap the baseline scorer itself uses, purely for this 0-100% bar display.
  const geoVelocityDisplayRatio = signals
    ? Math.min(signals.geo_velocity_kmh / 1000, 1)
    : null;

  const derivedBehavior = signals
    ? Math.round(((geoVelocityDisplayRatio + signals.device_mismatch_score) / 2) * 100)
    : null;

  const apiResponse = detail || {
    session_id: session.session_id,
    user_id: session.user_id,
    risk_score: session.risk_score,
    risk_tier: session.risk_tier,
    device_fingerprint: session.device_fingerprint,
    ip_address: session.ip_address,
    timestamp: session.timestamp,
  };

  return (
    <div className="session-inspector-panel">
      <header className="session-inspector-header">
        <div>
          <p className="eyebrow">SESSION BEHAVIOR ANALYSIS</p>
          <h2>Session {session.session_id}</h2>
          <span>{formatTime(session.timestamp)}</span>
        </div>

        <div className="session-risk-summary">
          <strong>{session.risk_score}</strong>
          <RiskBadge tier={session.risk_tier} />
          <small>RISK SCORE</small>
        </div>
      </header>

      <div className="session-analysis-layout">
        <div className="session-analysis-main">
          <div className="analysis-card-grid">
            <DetailCard icon="◫" title="Session metadata">
              <KeyValue label="User" value={session.user_id} />
              <KeyValue label="IP address" value={session.ip_address} />
              <KeyValue label="Timestamp" value={formatTime(session.timestamp)} />
              <KeyValue label="Risk tier" value={session.risk_tier.toUpperCase()} />
            </DetailCard>

            <DetailCard icon="◎" title="Geo-velocity analysis">
              {signals ? (
                <>
                  <KeyValue label="Velocity" value={`${signals.geo_velocity_kmh.toFixed(1)} km/h`} />
                  <KeyValue label="Location status" value={signals.geo_location_status} />
                  <SignalBar label="Relative velocity (capped at 1000 km/h)" value={geoVelocityDisplayRatio} tone="red" />
                  <KeyValue
                    label="Interpretation"
                    value={geoVelocityDisplayRatio >= 0.7 ? "High location-jump risk" : "Low location-jump risk"}
                  />
                </>
              ) : (
                <p className="analysis-unavailable">Detailed geo-velocity signal is not available for this session.</p>
              )}
            </DetailCard>

            <DetailCard icon="✕" title="Token analysis">
              {signals ? (
                <>
                  <KeyValue label="Token reuse detected" value={signals.token_reuse_flag ? "Yes" : "No"} />
                  <KeyValue
                    label="Risk factor"
                    value={signals.token_reuse_flag ? "High" : "Low"}
                  />
                </>
              ) : (
                <p className="analysis-unavailable">Token reuse detail is not available for this session.</p>
              )}
            </DetailCard>

            <DetailCard icon="✕" title="Behavioral analysis">
              {signals ? (
                <>
                  <KeyValue label="Login burst count" value={signals.login_burst_count} />
                  <SignalBar label="Behavior anomaly index" value={derivedBehavior / 100} tone="amber" />
                  <KeyValue
                    label="Activity pattern"
                    value={derivedBehavior >= 70 ? "Unusual activity" : derivedBehavior >= 40 ? "Needs review" : "Normal pattern"}
                  />
                  <small className="derived-note">Derived in the frontend from the real geo/device signals returned by the backend.</small>
                </>
              ) : (
                <p className="analysis-unavailable">Behavioral signal details are waiting for the backend detail response.</p>
              )}
            </DetailCard>

            <DetailCard icon="▣" title="Device fingerprint">
              <KeyValue label="Device ID" value={session.device_fingerprint} mono />
              {signals ? (
                <SignalBar label="Device mismatch score" value={signals.device_mismatch_score} tone="amber" />
              ) : (
                <p className="analysis-unavailable">Device mismatch score is not available for this session.</p>
              )}
            </DetailCard>

            <DetailCard icon="◒" title="Risk explanation">
              <div className="risk-explanation-number">{session.risk_score}</div>
              <div className="risk-distribution-track">
                <span style={{ width: `${session.risk_score}%` }} />
              </div>
              <div className="risk-distribution-labels"><span>0</span><strong>{session.risk_score}</strong><span>100</span></div>
            </DetailCard>
          </div>

          <section className="session-analysis-card timeline-card">
            <div className="session-analysis-title">
              <span className="analysis-icon">◷</span>
              <h4>Session event timeline</h4>
            </div>

            {history.length ? (
              <div className="timeline compact-analysis-timeline">
                {history.map((item, index) => (
                  <div className="timeline-item" key={`${item.event}-${index}`}>
                    <span className="timeline-dot" />
                    <div>
                      <strong>{item.event.replaceAll("_", " ")}</strong>
                      <span>{formatTime(item.timestamp)}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="analysis-unavailable">No history is available for this session yet.</p>
            )}
          </section>
        </div>

        <aside className="api-response-card">
          <div className="session-analysis-title">
            <span className="analysis-icon">{`{}`}</span>
            <h4>API response</h4>
          </div>
          <pre>{JSON.stringify(apiResponse, null, 2)}</pre>
        </aside>
      </div>
    </div>
  );
}