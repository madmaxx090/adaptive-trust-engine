import { useMemo, useState } from "react";
import RiskBadge from "../components/RiskBadge";
import SessionInspectorPanel from "../components/SessionInspectorPanel";

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export default function SessionsWorkspace({
  sessions,
  detailExample,
  selectedSession,
  onSelectSession,
  onNavigate,
  onToast,
}) {
  const [search, setSearch] = useState("");
  const [riskFilter, setRiskFilter] = useState("all");
  const [sortMode, setSortMode] = useState("newest");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = sessions.filter((session) => {
      const matchesSearch =
        !q ||
        session.user_id.toLowerCase().includes(q) ||
        session.session_id.toLowerCase().includes(q) ||
        session.ip_address.toLowerCase().includes(q);
      const matchesRisk = riskFilter === "all" || session.risk_tier === riskFilter;
      return matchesSearch && matchesRisk;
    });

    return [...rows].sort((a, b) => {
      if (sortMode === "risk-high") return b.risk_score - a.risk_score;
      if (sortMode === "risk-low") return a.risk_score - b.risk_score;
      return new Date(b.timestamp) - new Date(a.timestamp);
    });
  }, [sessions, search, riskFilter, sortMode]);

  const exportCsv = () => {
    const header = ["session_id", "user_id", "risk_score", "risk_tier", "ip_address", "timestamp"];
    const body = filtered.map((row) => header.map((key) => row[key]).join(","));
    const csv = [header.join(","), ...body].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "ate_sessions.csv";
    anchor.click();
    URL.revokeObjectURL(url);
    onToast?.("Session CSV exported");
  };

  return (
    <section className="page-stack sessions-workspace-page">
      <div className="panel sessions-toolbar-panel">
        <div className="toolbar advanced-toolbar">
          <div className="search-wrap">
            <span>⌕</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search session, user or IP"
            />
          </div>

          <select value={riskFilter} onChange={(event) => setRiskFilter(event.target.value)}>
            <option value="all">All risk tiers</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>

          <select value={sortMode} onChange={(event) => setSortMode(event.target.value)}>
            <option value="newest">Newest first</option>
            <option value="risk-high">Risk: high to low</option>
            <option value="risk-low">Risk: low to high</option>
          </select>

          <button className="secondary-button" type="button" onClick={exportCsv}>Export CSV</button>
          <button className="primary-button" type="button" onClick={() => onNavigate("score-test")}>+ Score session</button>
        </div>
      </div>

      <div className="session-workspace-grid">
        <section className="panel session-list-panel">
          <div className="panel-heading">
            <div>
              <h2>Live sessions</h2>
              <p>{filtered.length} session{filtered.length === 1 ? "" : "s"} match the current filters</p>
            </div>
          </div>

          <div className="session-card-list">
            {filtered.map((session) => {
              const active = selectedSession?.session_id === session.session_id;
              return (
                <button
                  type="button"
                  key={session.session_id}
                  className={`session-select-card ${active ? "active" : ""}`}
                  onClick={() => onSelectSession(session)}
                >
                  <div className="session-select-top">
                    <div>
                      <strong>{session.session_id}</strong>
                      <span>{session.user_id}</span>
                    </div>
                    <RiskBadge tier={session.risk_tier} />
                  </div>

                  <div className="session-select-bottom">
                    <span>{session.ip_address}</span>
                    <span>{formatTime(session.timestamp)}</span>
                    <b>{session.risk_score}</b>
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <section className="panel merged-session-detail-panel">
          <SessionInspectorPanel
            session={selectedSession || detailExample}
            detailExample={detailExample}
          />
        </section>
      </div>
    </section>
  );
}
