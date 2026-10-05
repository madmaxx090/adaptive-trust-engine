import RiskBadge from "./RiskBadge";

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export default function SessionTable({ sessions, onOpen, sortBy = "timestamp", sortDirection = "desc", onSort }) {
  const head = (key, label) => (
    <button className="table-head-button" type="button" onClick={() => onSort?.(key)}>
      {label}{sortBy === key ? (sortDirection === "asc" ? " ↑" : " ↓") : ""}
    </button>
  );

  return (
    <div className="table-wrap">
      <table className="session-table">
        <thead><tr>
          <th>{head("session_id", "Session ID")}</th>
          <th>{head("user_id", "User")}</th>
          <th>{head("risk_score", "Risk Score")}</th>
          <th>{head("risk_tier", "Risk Tier")}</th>
          <th>{head("ip_address", "IP Address")}</th>
          <th>{head("timestamp", "Timestamp")}</th>
        </tr></thead>
        <tbody>
          {sessions.map((session) => (
            <tr key={session.session_id}>
              <td><button className="row-link" type="button" onClick={() => onOpen(session)}>{session.session_id}</button></td>
              <td>{session.user_id}</td>
              <td><strong>{session.risk_score}</strong></td>
              <td><RiskBadge tier={session.risk_tier} /></td>
              <td>{session.ip_address}</td>
              <td>{formatTime(session.timestamp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
