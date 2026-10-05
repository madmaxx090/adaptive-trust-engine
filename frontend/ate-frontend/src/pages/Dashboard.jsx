import { useEffect, useMemo, useState } from "react";
import SessionTable from "../components/SessionTable";
import RiskBadge from "../components/RiskBadge";

const RADAR_POSITIONS = [
  { left: 25, top: 34 },
  { left: 62, top: 24 },
  { left: 71, top: 62 },
  { left: 38, top: 70 },
  { left: 48, top: 43 },
  { left: 23, top: 60 },
];

function postureLabel(score) {
  if (score >= 75) return "Strong";
  if (score >= 50) return "Guarded";
  return "Exposed";
}

function ThreatRadar({ sessions, activeSession, onOpenSession }) {
  return (
    <div className="threat-radar-shell">
      <div className="radar-title-row">
        <div>
          <span className="command-kicker">INTERACTIVE</span>
          <h3>Session Threat Radar</h3>
        </div>
        <span className="demo-chip">DEMO VISUALIZATION</span>
      </div>

      <div className="threat-radar">
        <div className="radar-grid radar-grid-1" />
        <div className="radar-grid radar-grid-2" />
        <div className="radar-cross radar-cross-x" />
        <div className="radar-cross radar-cross-y" />
        <div className="radar-sweep" />
        <div className="radar-core">ATE</div>

        {sessions.map((session, index) => {
          const pos = RADAR_POSITIONS[index % RADAR_POSITIONS.length];
          const size = 12 + Math.round(session.risk_score / 10);
          const isActive = activeSession?.session_id === session.session_id;

          return (
            <button
              key={session.session_id}
              type="button"
              className={`radar-blip blip-${session.risk_tier} ${isActive ? "active" : ""}`}
              style={{
                left: `${pos.left}%`,
                top: `${pos.top}%`,
                width: `${size}px`,
                height: `${size}px`,
              }}
              onClick={() => onOpenSession(session)}
              title={`${session.session_id} · ${session.risk_score} · ${session.risk_tier}`}
            >
              <span />
            </button>
          );
        })}
      </div>

      <div className="radar-legend">
        <span><i className="legend-dot low" /> Low</span>
        <span><i className="legend-dot medium" /> Medium</span>
        <span><i className="legend-dot high" /> High</span>
      </div>
    </div>
  );
}

function SignalInsight({ label, risk, explanation }) {
  const percent = Math.max(0, Math.min(100, Math.round(risk * 100)));
  return (
    <div className="explain-row">
      <div className="explain-row-head">
        <span>{label}</span>
        <strong>{percent}% risk</strong>
      </div>
      <div className="explain-meter">
        <span style={{ width: `${percent}%` }} />
      </div>
      <small>{explanation}</small>
    </div>
  );
}

export default function Dashboard({
  sessions,
  selectedDetail,
  detailLoading,
  detailError,
  onNavigate,
  onOpenSession,
}) {
  const [replaying, setReplaying] = useState(false);
  const [replayIndex, setReplayIndex] = useState(-1);

  const averageRisk = useMemo(
    () =>
      sessions.reduce((sum, item) => sum + item.risk_score, 0) /
      Math.max(sessions.length, 1),
    [sessions]
  );

  const defenseScore = Math.round(100 - averageRisk);
  const highCount = sessions.filter((s) => s.risk_tier === "high").length;
  const suspiciousCount = sessions.filter((s) => s.risk_tier !== "low").length;

  const highestRiskSession = useMemo(
    () => [...sessions].sort((a, b) => b.risk_score - a.risk_score)[0],
    [sessions]
  );

  const activeSession =
    replayIndex >= 0 ? sessions[replayIndex] : highestRiskSession;

  useEffect(() => {
    if (!replaying) return undefined;

    const timer = window.setInterval(() => {
      setReplayIndex((current) => {
        const next = current + 1;

        if (next >= sessions.length) {
          setReplaying(false);
          return sessions.length - 1;
        }

        return next;
      });
    }, 1150);

    return () => window.clearInterval(timer);
  }, [replaying, sessions.length]);

  const startReplay = () => {
    setReplayIndex(0);
    setReplaying(true);
  };

  const stopReplay = () => {
    setReplaying(false);
  };

  return (
    <section className="page-stack dashboard-command-page">
      <div className="command-hero">
        <div className="command-copy">
          <div className="command-label-row">
            <span className="command-kicker">ADAPTIVE SECURITY INTELLIGENCE</span>
            <span className="pulse-label"><i /> SIMULATION READY</span>
          </div>

          <h2>
            Threat Command
            <span> Center</span>
          </h2>

          <p>
            One screen to observe session risk, replay authentication events,
            inspect explainability signals and jump directly into suspicious sessions.
          </p>

          <div className="command-actions">
            <button className="command-primary" type="button" onClick={replaying ? stopReplay : startReplay}>
              <span className="play-icon">{replaying ? "■" : "▶"}</span>
              {replaying ? "Stop Threat Replay" : "Start Threat Replay"}
            </button>

            <button className="command-secondary" type="button" onClick={() => onNavigate("risk-analytics")}>
              Open Risk Analytics →
            </button>
          </div>

          <div className="hero-microstats">
            <div>
              <span>Defense posture</span>
              <strong>{postureLabel(defenseScore)}</strong>
            </div>
            <div>
              <span>Suspicious sessions</span>
              <strong>{suspiciousCount}/{sessions.length}</strong>
            </div>
            <div>
              <span>High-risk alerts</span>
              <strong>{highCount}</strong>
            </div>
          </div>
        </div>

        <div className="defense-orb-card">
          <div className="defense-orb">
            <div className="orb-ring orb-ring-1" />
            <div className="orb-ring orb-ring-2" />
            <div className="orb-ring orb-ring-3" />
            <div className="orb-center">
              <span>Defense</span>
              <strong>{defenseScore}</strong>
              <small>/ 100</small>
            </div>
          </div>
          <p>
            Derived from current live risk average
            <strong>{averageRisk.toFixed(1)}</strong>
          </p>
        </div>
      </div>

      <div className="command-grid">
        <div className="panel command-panel radar-panel">
          <ThreatRadar
            sessions={sessions}
            activeSession={activeSession}
            onOpenSession={onOpenSession}
          />
        </div>

        <div className="panel command-panel replay-panel">
          <div className="panel-heading command-heading">
            <div>
              <span className="command-kicker">THREAT REPLAY</span>
              <h2>Authentication Event</h2>
              <p>Animated walkthrough of your live session stream</p>
            </div>
            <span className={`replay-status ${replaying ? "playing" : ""}`}>
              {replaying ? "PLAYING" : "PAUSED"}
            </span>
          </div>

          <div className="replay-session-card">
            <div className="replay-score">
              <span>Risk</span>
              <strong>{activeSession?.risk_score ?? "—"}</strong>
            </div>

            <div className="replay-main">
              <strong>{activeSession?.session_id ?? "No session"}</strong>
              <span>{activeSession?.user_id ?? "—"}</span>
              <span>{activeSession?.ip_address ?? "—"}</span>
            </div>

            {activeSession && <RiskBadge tier={activeSession.risk_tier} />}
          </div>

          <div className="replay-track">
            {sessions.map((session, index) => (
              <button
                key={session.session_id}
                type="button"
                className={`replay-node node-${session.risk_tier} ${index === replayIndex ? "active" : ""}`}
                onClick={() => {
                  setReplayIndex(index);
                  setReplaying(false);
                }}
                title={session.session_id}
              >
                <span />
              </button>
            ))}
          </div>

          <div className="replay-footer">
            <span>
              {replayIndex >= 0 ? `Event ${replayIndex + 1} of ${sessions.length}` : "Highest-risk session selected"}
            </span>
            {activeSession && (
              <button type="button" onClick={() => onOpenSession(activeSession)}>
                Inspect session →
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="command-grid command-grid-lower">
        <div className="panel">
          <div className="panel-heading">
            <div>
              <span className="command-kicker">EXPLAINABLE AI SPOTLIGHT</span>
              <h2>{selectedDetail ? `Why ${selectedDetail.session_id} was flagged` : "Explainability spotlight"}</h2>
              <p>Transparent risk reasoning from the live session detail endpoint</p>
            </div>
            {selectedDetail && <RiskBadge tier={selectedDetail.risk_tier} />}
          </div>

          {detailLoading ? (
            <p className="analysis-unavailable">Loading session detail from the backend...</p>
          ) : detailError ? (
            <p className="analysis-unavailable">Unable to load session detail: {detailError}</p>
          ) : selectedDetail ? (
            <>
              <div className="explainability-stack">
                <SignalInsight
                  label="Geo velocity anomaly"
                  risk={Math.min(selectedDetail.contributing_signals.geo_velocity_kmh / 1000, 1)}
                  explanation={`Observed ${selectedDetail.contributing_signals.geo_velocity_kmh.toFixed(1)} km/h (location status: ${selectedDetail.contributing_signals.geo_location_status}). A high value suggests the login movement pattern is geographically unusual.`}
                />
                <SignalInsight
                  label="Device mismatch"
                  risk={selectedDetail.contributing_signals.device_mismatch_score}
                  explanation={selectedDetail.contributing_signals.device_mismatch_score > 0
                    ? "The observed device differs strongly from the expected session identity."
                    : "The observed device matches the expected session identity."}
                />
                <SignalInsight
                  label="Token reuse"
                  risk={selectedDetail.contributing_signals.token_reuse_flag ? 1 : 0}
                  explanation={selectedDetail.contributing_signals.token_reuse_flag
                    ? "A reused token is present in this session and contributes to its risk result."
                    : "No token reuse was detected for this session."}
                />
                <SignalInsight
                  label="Login burst anomaly"
                  risk={Math.min(selectedDetail.contributing_signals.login_burst_count / 30, 1)}
                  explanation={`Observed ${selectedDetail.contributing_signals.login_burst_count} rapid logins in the burst window (scorer cap: 30).`}
                />
              </div>

              <button className="insight-link" type="button" onClick={() => onOpenSession(selectedDetail)}>
                Open explainability inspector →
              </button>
            </>
          ) : (
            <p className="analysis-unavailable">
              No session detail loaded yet — open a session from the radar, replay feed, or session list to spotlight its explanation here.
            </p>
          )}
        </div>

        <div className="panel pulse-panel">
          <div className="panel-heading">
            <div>
              <span className="command-kicker">RISK PULSE</span>
              <h2>Current session stream</h2>
              <p>Every pulse is clickable</p>
            </div>
          </div>

          <div className="risk-pulse-list">
            {sessions.map((session) => (
              <button
                key={session.session_id}
                type="button"
                className={`risk-pulse-item pulse-${session.risk_tier}`}
                onClick={() => onOpenSession(session)}
              >
                <span className="risk-pulse-wave" />
                <div>
                  <strong>{session.session_id}</strong>
                  <small>{session.user_id}</small>
                </div>
                <b>{session.risk_score}</b>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-heading">
          <div>
            <span className="command-kicker">QUICK INSPECT</span>
            <h2>Recent sessions</h2>
            <p>Select a session ID to open its behavior analysis inside Sessions</p>
          </div>
          <button className="text-button" type="button" onClick={() => onNavigate("sessions")}>
            View all
          </button>
        </div>
        <SessionTable sessions={sessions.slice(0, 5)} onOpen={onOpenSession} />
      </div>
    </section>
  );
}
