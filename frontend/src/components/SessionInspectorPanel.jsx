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

export default function SessionInspectorPanel({ session, detailExample }) {
  if (!session) {
    return (
      <div className="session-empty-inspector">
        <div className="empty-inspector-icon">⌁</div>
        <h3>Select a session</h3>
        <p>Click a session in the list to inspect its behavior and risk signals.</p>
      </div>
    );
  }

  const hasDetailedSignals = session.session_id === detailExample.session_id;
  const detail = hasDetailedSignals ? detailExample : session;
  const signals = hasDetailedSignals ? detailExample.contributing_signals : null;
  const history = hasDetailedSignals ? detailExample.history : [];

  const derivedBehavior = signals
    ? Math.round(((signals.geo_velocity_score + signals.device_mismatch_score) / 2) * 100)
    : null;

  const apiResponse = hasDetailedSignals
    ? detailExample
    : {
        session_id: session.session_id,
        user_id: session.user_id,
        risk_score: session.risk_score,
        risk_tier: session.risk_tier,
        device_fingerprint: session.device_fingerprint,
        ip_address: session.ip_address,
        timestamp: session.timestamp,
        note: "Detailed contributing signals are not present for this session in mock_data.json.",
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
                  <SignalBar label="Geo velocity score" value={signals.geo_velocity_score} tone="red" />
                  <KeyValue
                    label="Interpretation"
                    value={signals.geo_velocity_score >= 0.7 ? "High location-jump risk" : "Low location-jump risk"}
                  />
                </>
              ) : (
                <p className="analysis-unavailable">Detailed geo-velocity signal is not available for this mock session.</p>
              )}
            </DetailCard>

            <DetailCard icon="⌁" title="Token analysis">
              {signals ? (
                <>
                  <KeyValue label="Token reuse detected" value={signals.token_reuse_flag ? "Yes" : "No"} />
                  <KeyValue
                    label="Risk factor"
                    value={signals.token_reuse_flag ? "High" : "Low"}
                  />
                </>
              ) : (
                <p className="analysis-unavailable">Token reuse detail is not available for this mock session.</p>
              )}
            </DetailCard>

            <DetailCard icon="⌁" title="Behavioral analysis">
              {signals ? (
                <>
                  <SignalBar label="Behavior anomaly index" value={derivedBehavior / 100} tone="amber" />
                  <KeyValue
                    label="Activity pattern"
                    value={derivedBehavior >= 70 ? "Unusual activity" : derivedBehavior >= 40 ? "Needs review" : "Normal pattern"}
                  />
                  <small className="derived-note">Derived in the frontend from the available geo/device mock signals.</small>
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
                <p className="analysis-unavailable">Device mismatch score is not available for this mock session.</p>
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
              <p className="analysis-unavailable">No history array is available for this session in the current mock dataset.</p>
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
