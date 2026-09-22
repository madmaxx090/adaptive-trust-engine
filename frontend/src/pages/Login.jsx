import { useState } from "react";

export default function Login({ onSignIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const submit = (event) => {
    event.preventDefault();
    setError("");

    if (!email.trim() || !password.trim()) {
      setError("Enter an email address and password to continue.");
      return;
    }

    onSignIn({ email: email.trim() });
  };

  return (
    <main className="login-page">
      <div className="login-shell">
        <section className="login-brand-panel">
          <div className="login-brand-mark">A</div>
          <p className="login-brand-name">ATE</p>
          <h1>Protect every session with adaptive threat intelligence</h1>
          <p className="login-brand-copy">
            Adaptive Token Engine analyzes session behavior, device identity and
            token signals to explain authentication risk.
          </p>
          <div className="login-brand-pills">
            <span>Risk scoring</span>
            <span>Behavior analysis</span>
            <span>Explainable signals</span>
          </div>
        </section>

        <section className="login-form-panel">
          <div className="login-form-wrap">
            <span className="login-demo-badge">FYP FRONTEND DEMO</span>
            <h2>Sign in to ATE</h2>
            <p>Enter your credentials to access the security dashboard.</p>

            <form onSubmit={submit} className="login-form">
              <label>
                Email address
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="you@company.com"
                  autoComplete="email"
                />
              </label>

              <label>
                Password
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="Enter your password"
                  autoComplete="current-password"
                />
              </label>

              {error && <div className="login-error">{error}</div>}

              <button className="login-submit" type="submit">
                Sign in
              </button>
            </form>

            <div className="login-demo-note">
              <strong>Demo note:</strong> authentication is frontend-only until a
              backend login endpoint is added to the ATE API contract.
            </div>

            <footer>© 2026 Adaptive Token Engine · Final Year Project</footer>
          </div>
        </section>
      </div>
    </main>
  );
}
