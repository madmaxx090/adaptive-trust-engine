import { useState } from "react";

function KeyCard({ title, description, value }) {
  const [copied, setCopied] = useState(false);
  const copyValue = async () => {
    try { await navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1200); }
    catch { setCopied(false); }
  };
  return <div className="panel key-card"><div className="panel-heading"><div><h2>{title}</h2><p>{description}</p></div><span className="key-status">UI mock</span></div><div className="key-value mono">{value}</div><button className="secondary-button" type="button" onClick={copyValue}>{copied ? "Copied" : "Copy display value"}</button></div>;
}

export default function ApiKeys() {
  return (
    <section className="page-stack">
      <div className="notice">API-key management is not defined in the supplied ATE backend contract. This screen is intentionally presentation-only until the backend adds secure key-management endpoints.</div>
      <div className="key-grid"><KeyCard title="Production API Key" description="Demonstration credential" value="ate_prod_••••••••••••••••" /><KeyCard title="Staging API Key" description="Demonstration credential" value="ate_stage_••••••••••••••" /></div>
      <div className="panel"><div className="panel-heading"><div><h2>Security guidance</h2><p>Frontend presentation only</p></div></div><div className="safety-grid"><div className="info-card"><span>Storage</span><strong>Never place real secret keys in frontend source code.</strong></div><div className="info-card"><span>Rotation</span><strong>Use backend-controlled rotation when implemented.</strong></div><div className="info-card"><span>Status</span><strong>No real API-key backend exists in the current contract.</strong></div></div></div>
    </section>
  );
}
