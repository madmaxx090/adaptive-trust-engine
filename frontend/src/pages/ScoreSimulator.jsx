import { useState } from "react";
import RiskBadge from "../components/RiskBadge";
import { getDeviceFingerprint } from "../utils/fingerprint";

export default function ScoreSimulator({ scoreExample, onToast }) {
  const [form, setForm] = useState({ user_id: "user_107", ip_address: "39.42.10.25", device_fingerprint: "" });
  const [result, setResult] = useState(null);
  const [fingerprintLoading, setFingerprintLoading] = useState(false);

  const generateFingerprint = async () => {
    try {
      setFingerprintLoading(true);
      const id = await getDeviceFingerprint();
      setForm((current) => ({ ...current, device_fingerprint: id }));
      onToast?.("Fingerprint generated");
    } catch (error) {
      onToast?.("Fingerprint generation failed", "error");
      console.error(error);
    } finally { setFingerprintLoading(false); }
  };

  const submit = (event) => {
    event.preventDefault();
    setResult({ ...scoreExample, submittedInput: form });
    onToast?.("Mock session scored");
  };

  return (
    <section className="two-column">
      <form className="panel score-form" onSubmit={submit}>
        <div className="panel-heading"><div><h2>Score a session</h2><p>Frontend simulator for POST /session/score</p></div></div>
        <label>User ID<input value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} /></label>
        <label>IP Address<input value={form.ip_address} onChange={(e) => setForm({ ...form, ip_address: e.target.value })} /></label>
        <label>Device Fingerprint<input value={form.device_fingerprint} onChange={(e) => setForm({ ...form, device_fingerprint: e.target.value })} placeholder="Generate or enter a fingerprint" /></label>
        <div className="button-row"><button className="secondary-button" type="button" onClick={generateFingerprint} disabled={fingerprintLoading}>{fingerprintLoading ? "Generating…" : "Generate fingerprint"}</button><button className="primary-button" type="submit">Run mock score</button></div>
        <div className="notice neutral">The backend is not connected yet, so this screen currently displays the supplied mock response.</div>
      </form>

      <div className="panel">
        <div className="panel-heading"><div><h2>Risk result</h2><p>Visual response preview</p></div></div>
        {result ? (
          <div className="simulator-result">
            <div className="result-score"><div><span>Risk Score</span><strong>{result.risk_score}</strong></div><RiskBadge tier={result.risk_tier} /></div>
            <div className="result-signals"><div><span>Geo velocity</span><strong>{result.contributing_signals.geo_velocity_score}</strong></div><div><span>Device mismatch</span><strong>{result.contributing_signals.device_mismatch_score}</strong></div><div><span>Token reuse</span><strong>{result.contributing_signals.token_reuse_flag ? "Yes" : "No"}</strong></div></div>
          </div>
        ) : <div className="empty-state tall">Complete the form and press “Run mock score”.</div>}
      </div>
    </section>
  );
}
