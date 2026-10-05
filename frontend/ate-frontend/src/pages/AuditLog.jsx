import { useEffect, useState } from "react";
import RiskBadge from "../components/RiskBadge";
import { getAuditLog } from "../services/api";

const PAGE_SIZE = 100;

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

export default function AuditLog({ sessions, onOpenSession }) {
  const [entries, setEntries] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        setLoading(true);
        setError("");
        const data = await getAuditLog({ limit: PAGE_SIZE });
        if (cancelled) return;
        setEntries(data.entries);
        setTotal(data.total);
      } catch (err) {
        if (!cancelled) setError(err.message || "Failed to load audit log");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => { cancelled = true; };
  }, []);

  // An audit entry only links to a session that is present in the loaded list;
  // session_id is nullable in the audit table, and the session list is paged.
  const resolveSession = (entry) =>
    entry.session_id
      ? sessions.find((session) => session.session_id === entry.session_id)
      : undefined;

  const openEvent = (entry) => {
    const matching = resolveSession(entry);
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

        {error ? (
          <div className="api-error-state">
            <strong>Unable to load audit log</strong>
            <p>{error}</p>
            <small>Check that the backend is reachable at http://localhost:8008, then reload.</small>
          </div>
        ) : loading ? (
          <div className="empty-state tall">Loading audit trail…</div>
        ) : entries.length === 0 ? (
          <div className="empty-state tall">
            No audit events yet. Entries are written when a session is scored, so sessions
            scored before the audit trail was wired up do not appear here.
          </div>
        ) : (
          <>
            <p className="list-summary">
              Showing {entries.length} of {total} audit events from GET /audit-log
            </p>
            <div className="audit-list clickable-audit-list">
              {entries.map((entry, index) => {
                const clickable = Boolean(resolveSession(entry));
                return (
                  <button
                    type="button"
                    className="audit-item audit-item-button"
                    key={entry.id}
                    onClick={() => openEvent(entry)}
                    disabled={!clickable}
                    title={clickable ? undefined : "This session is not in the currently loaded session list"}
                  >
                    <div className="audit-icon">{index + 1}</div>
                    <div className="audit-main">
                      <strong>{entry.event_type.replaceAll("_", " ")}</strong>
                      <span>
                        {entry.session_id || "no session"} · {entry.user_id || "unknown user"}
                      </span>
                    </div>
                    <div className="audit-right">
                      {entry.risk_tier ? <RiskBadge tier={entry.risk_tier} /> : <span>—</span>}
                      <time>{formatTime(entry.timestamp)}</time>
                      {clickable ? (
                        <span className="audit-open-hint">View details →</span>
                      ) : (
                        <span className="audit-open-hint">Not loaded</span>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
