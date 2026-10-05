const NAV_ITEMS = [
  { id: "dashboard", label: "Dashboard", icon: "▦" },
  { id: "sessions", label: "Sessions", icon: "⌁" },
  { id: "risk-analytics", label: "Risk Analytics", icon: "◒" },
  { id: "score-test", label: "Score Simulator", icon: "⚡" },
  { id: "audit-log", label: "Audit Activity", icon: "○" },
  { id: "api-system", label: "API & System", icon: "<>" },
  { id: "api-keys", label: "API Keys", icon: "⌘" },
];

export default function Sidebar({
  activePage,
  onNavigate,
  collapsed,
  onToggle,
  onSignOut,
}) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">A</div>
        {!collapsed && (
          <div>
            <strong>ATE</strong>
            <span>Adaptive Token Engine</span>
          </div>
        )}
      </div>

      <nav className="nav" aria-label="ATE navigation">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`nav-button ${activePage === item.id ? "active" : ""}`}
            onClick={() => onNavigate(item.id)}
            title={collapsed ? item.label : undefined}
          >
            <span className="nav-icon">{item.icon}</span>
            {!collapsed && <span>{item.label}</span>}
          </button>
        ))}
      </nav>

      <div className="sidebar-footer-actions">
        <button
          className="signout-button"
          type="button"
          onClick={onSignOut}
          title={collapsed ? "Sign out" : undefined}
        >
          <span className="nav-icon">↪</span>
          {!collapsed && <span>Sign out</span>}
        </button>

        <button className="collapse-button" type="button" onClick={onToggle}>
          {collapsed ? "›" : "‹"}
        </button>
      </div>
    </aside>
  );
}
