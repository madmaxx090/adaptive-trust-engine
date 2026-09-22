import RiskBadge from "../components/RiskBadge";

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function AuditLog({ sessions, detailExample, onOpenSession }) {
  const events = [
    ...detailExample.history.map((item, index) => ({
      id: `detail-${index}`,
      type: item.event,
      session: detailExample.session_id,
      user: detailExample.user_id,
      timestamp: item.timestamp,
      tier: detailExample.risk_tier,
    })),
    ...sessions.map((session) => ({
      id: `risk-${session.session_id}`,
      type: "risk_evaluated",
      session: session.session_id,
      user: session.user_id,
      timestamp: session.timestamp,
      tier: session.risk_tier,
    })),
  ].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

  const openEvent = (item) => {
    const matching = sessions.find((session) => session.session_id === item.session);
    if (matching) onOpenSession(matching);
  };

  return (
    <section className="page-stack">
      <div className="panel">
        <div className="panel-heading">
          <div>
            <h2>Security activity</h2>
            <p>Click any audit event to open that session inside the merged Sessions workspace.</p>
          </div>
        </div>

        <div className="audit-list clickable-audit-list">
          {events.map((item, index) => (
            <button
              type="button"
              className="audit-item audit-item-button"
              key={item.id}
              onClick={() => openEvent(item)}
            >
              <div className="audit-icon">{index + 1}</div>
              <div className="audit-main">
                <strong>{item.type.replaceAll("_", " ")}</strong>
                <span>{item.session} · {item.user}</span>
              </div>
              <div className="audit-right">
                <RiskBadge tier={item.tier} />
                <time>{formatTime(item.timestamp)}</time>
                <span className="audit-open-hint">View details →</span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
