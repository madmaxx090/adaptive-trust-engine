import { useEffect, useState } from "react";
import "./App.css";

import Sidebar from "./components/Sidebar";
import PageHeader from "./components/PageHeader";

import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import SessionsWorkspace from "./pages/SessionsWorkspace";
import RiskAnalytics from "./pages/RiskAnalytics";
import ScoreSimulator from "./pages/ScoreSimulator";
import AuditLog from "./pages/AuditLog";
import ApiSystem from "./pages/ApiSystem";
import ApiKeys from "./pages/ApiKeys";

import { getSessions, getSessionDetail } from "./services/api";

const PAGE_INFO = {
  dashboard: {
    title: "Dashboard",
    subtitle: "Adaptive threat intelligence command center",
  },
  sessions: {
    title: "Sessions",
    subtitle: "Live session list and behavioral analysis in one workspace",
  },
  "risk-analytics": {
    title: "Risk Analytics",
    subtitle: "Analyze risk distribution and suspicious-session patterns",
  },
  "score-test": {
    title: "Score Simulator",
    subtitle: "Build and preview a POST /session/score request",
  },
  "audit-log": {
    title: "Audit Activity",
    subtitle: "Click any event to inspect the related session",
  },
  "api-system": {
    title: "API & System",
    subtitle: "Explore contract endpoints and test backend availability",
  },
  "api-keys": {
    title: "API Keys",
    subtitle: "Presentation-only key-management interface",
  },
};

export default function App() {
  // Real session list, fetched from the backend
  const [sessions, setSessions] = useState([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState("");

  // Currently selected session (from the list) + its real detail (fetched separately)
  const [selectedSession, setSelectedSession] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  const [signedIn, setSignedIn] = useState(
    () => window.sessionStorage.getItem("ate_demo_signed_in") === "1"
  );
  const [activePage, setActivePage] = useState("dashboard");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [toast, setToast] = useState(null);
  const [signedInUser, setSignedInUser] = useState(
    () => window.sessionStorage.getItem("ate_demo_user") || ""
  );

  const showToast = (message, tone = "success") => {
    setToast({ message, tone });
    window.setTimeout(() => setToast(null), 2200);
  };

  // Fetch real sessions from the backend once signed in
  useEffect(() => {
    if (!signedIn) return;

    const loadSessions = async () => {
      try {
        setSessionsLoading(true);
        setSessionsError("");
        const data = await getSessions();
        setSessions(data.sessions);
      } catch (err) {
        setSessionsError(err.message || "Failed to load sessions");
      } finally {
        setSessionsLoading(false);
      }
    };

    loadSessions();
  }, [signedIn]);

  const signIn = ({ email }) => {
    window.sessionStorage.setItem("ate_demo_signed_in", "1");
    window.sessionStorage.setItem("ate_demo_user", email);
    setSignedInUser(email);
    setSignedIn(true);
    setActivePage("dashboard");
  };

  const signOut = () => {
    window.sessionStorage.removeItem("ate_demo_signed_in");
    window.sessionStorage.removeItem("ate_demo_user");
    setSignedIn(false);
    setActivePage("dashboard");
  };

  // Fetch real detail whenever a session is selected -- replaces the old
  // "detailExample" single-mock-session pattern entirely.
  const selectSession = async (session) => {
    setSelectedSession(session);
    setSelectedDetail(null);
    setDetailError("");

    try {
      setDetailLoading(true);
      const detail = await getSessionDetail(session.session_id);
      setSelectedDetail(detail);
    } catch (err) {
      setDetailError(err.message || "Failed to load session detail");
    } finally {
      setDetailLoading(false);
    }
  };

  const openSession = (session) => {
    selectSession(session);
    setActivePage("sessions");
  };

  if (!signedIn) {
    return <Login onSignIn={signIn} />;
  }

  const page = PAGE_INFO[activePage] || PAGE_INFO.dashboard;

  return (
    <div className={`shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <Sidebar
        activePage={activePage}
        onNavigate={setActivePage}
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((value) => !value)}
        onSignOut={signOut}
      />

      <main className="main">
        <PageHeader
          title={page.title}
          subtitle={page.subtitle}
          statusText={signedInUser ? `Demo · ${signedInUser}` : "Live backend connected"}
        />

        {sessionsLoading ? (
          <div className="loading-box">
            <div className="spinner"></div>
            <p>Loading sessions from backend...</p>
          </div>
        ) : sessionsError ? (
          <div className="error-box">
            <strong>Unable to load sessions</strong>
            <p>{sessionsError}</p>
          </div>
        ) : (
          <>
            {activePage === "dashboard" && (
              <Dashboard
                sessions={sessions}
                selectedDetail={selectedDetail}
                detailLoading={detailLoading}
                detailError={detailError}
                onNavigate={setActivePage}
                onOpenSession={openSession}
              />
            )}

            {activePage === "sessions" && (
              <SessionsWorkspace
                sessions={sessions}
                selectedSession={selectedSession}
                selectedDetail={selectedDetail}
                detailLoading={detailLoading}
                detailError={detailError}
                onSelectSession={selectSession}
                onNavigate={setActivePage}
                onToast={showToast}
              />
            )}

            {activePage === "risk-analytics" && (
              <RiskAnalytics sessions={sessions} />
            )}

            {activePage === "score-test" && (
              <ScoreSimulator onToast={showToast} />
            )}

            {activePage === "audit-log" && (
              <AuditLog
                sessions={sessions}
                onOpenSession={openSession}
              />
            )}

            {activePage === "api-system" && (
              <ApiSystem onToast={showToast} />
            )}

            {activePage === "api-keys" && <ApiKeys />}
          </>
        )}

        {toast && <div className={`toast toast-${toast.tone}`}>{toast.message}</div>}
      </main>
    </div>
  );
}