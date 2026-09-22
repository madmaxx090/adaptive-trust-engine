export default function PageHeader({ title, subtitle, statusText = "Mock data connected" }) {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow">ATE SECURITY CONSOLE</p>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      <div className="header-status"><span className="status-dot" />{statusText}</div>
    </header>
  );
}
