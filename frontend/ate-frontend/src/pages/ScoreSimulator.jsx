import { useState } from "react";
import RiskBadge from "../components/RiskBadge";
import { getDeviceFingerprint } from "../utils/fingerprint";
import { scoreSession } from "../services/api";

const EMPTY_FORM = { user_id: "user_107", ip_address: "39.42.10.25", device_fingerprint: "" };

export default function ScoreSimulator({ onToast }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [result, setResult] = useState(null);
  const [scoring, setScoring] = useState(false);
  const [scoreError, setScoreError] = useState("");
  const [fingerprintLoading, setFingerprintLoading] = useState(false);

  // The backend rejects empty required fields with a 422 whose `detail` is an
  // array of Pydantic errors, which does not render usefully -- gate locally.
  const canSubmit =
    form.user_id.trim() !== "" &&
    form.ip_address.trim() !== "" &&
    form.device_fingerprint.trim() !== "";

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

  const submit = async (event) => {
    event.preventDefault();
    setScoring(true);
    setScoreError("");

    try {
      const data = await scoreSession({
        user_id: form.user_id.trim(),
        ip_address: form.ip_address.trim(),
        device_fingerprint: form.device_fingerprint.trim(),
      });
      setResult(data);
      onToast?.("Session scored");
    } catch (error) {
      setResult(null);
      setScoreError(error.message || "Backend request failed");
      onToast?.("Scoring request failed", "error");
    } finally {
      setScoring(false);
    }
  };

  // Defensive: a partial or unexpected response must never blank-screen the
  // page, so every signal read tolerates a missing object or missing field.
  const signals = result?.contributing_signals ?? {};
  const formatSignal = (value, suffix = "") =>
    typeof value === "number" ? `${value.toFixed(1)}${suffix}` : "—";

  return (
    <section className="two-column">
      <form className="panel score-form" onSubmit={submit}>
        <div className="panel-heading"><div><h2>Score a session</h2><p>Live request to POST /session/score</p></div></div>
        <label>User ID<input value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} /></label>
        <label>IP Address<input value={form.ip_address} onChange={(e) => setForm({ ...form, ip_address: e.target.value })} /></label>
        <label>Device Fingerprint<input value={form.device_fingerprint} onChange={(e) => setForm({ ...form, device_fingerprint: e.target.value })} placeholder="Generate or enter a fingerprint" /></label>
        <div className="button-row"><button className="secondary-button" type="button" onClick={generateFingerprint} disabled={fingerprintLoading}>{fingerprintLoading ? "Generating…" : "Generate fingerprint"}</button><button className="primary-button" type="submit" disabled={scoring || !canSubmit}>{scoring ? "Scoring…" : "Run score"}</button></div>
        <div className="notice neutral">This calls the real backend and persists a session with its risk event, so the new session appears in the Sessions workspace after a refresh.</div>
      </form>

      <div className="panel">
        <div className="panel-heading"><div><h2>Risk result</h2><p>Live response preview</p></div></div>
        {scoreError ? (
          <div className="api-error-state">
            <strong>Scoring request failed</strong>
            <p>{scoreError}</p>
            <small>Check that the backend is reachable at http://localhost:8008, then retry.</small>
          </div>
        ) : scoring ? (
          <div className="empty-state tall">Scoring session…</div>
        ) : result ? (
          <div className="simulator-result">
            <div className="result-score"><div><span>Risk Score</span><strong>{result.risk_score ?? "—"}</strong></div><RiskBadge tier={result.risk_tier ?? "unknown"} /></div>
            <div className="result-signals">
              <div><span>Geo velocity</span><strong>{formatSignal(signals.geo_velocity_kmh, " km/h")}</strong></div>
              <div><span>Device mismatch</span><strong>{formatSignal(signals.device_mismatch_score)}</strong></div>
              <div><span>Token reuse</span><strong>{signals.token_reuse_flag === true ? "Yes" : signals.token_reuse_flag === false ? "No" : "—"}</strong></div>
              <div><span>Location status</span><strong>{signals.geo_location_status ?? "—"}</strong></div>
              <div><span>Login burst</span><strong>{signals.login_burst_count ?? "—"}</strong></div>
              <div><span>ML anomaly</span><strong>{result.ml_anomaly_flag === true ? "Flagged" : result.ml_anomaly_flag === false ? "Normal" : "—"}</strong></div>
            </div>
            <p className="helper-text">{result.session_id ? `Session ${result.session_id}` : "No session id returned"}</p>
          </div>
        ) : <div className="empty-state tall">Complete the form and press “Run score”.</div>}
      </div>
    </section>
  );
}
