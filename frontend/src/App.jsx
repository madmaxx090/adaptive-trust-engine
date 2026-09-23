import { useState } from "react";
import "./App.css";
import mockData from "./data/mock_data.json";

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
  const sessions = mockData.sessions_list_response.sessions;
  const detailExample = mockData.session_detail_response_example;
  const scoreExample = mockData.session_score_response_example;

  const [signedIn, setSignedIn] = useState(
    () => window.sessionStorage.getItem("ate_demo_signed_in") === "1"
  );
  const [activePage, setActivePage] = useState("dashboard");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [selectedSession, setSelectedSession] = useState(detailExample);
  const [toast, setToast] = useState(null);
  const [signedInUser, setSignedInUser] = useState(
    () => window.sessionStorage.getItem("ate_demo_user") || ""
  );

  const showToast = (message, tone = "success") => {
    setToast({ message, tone });
    window.setTimeout(() => setToast(null), 2200);
  };

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

  const selectSession = (session) => {
    setSelectedSession(
      session.session_id === detailExample.session_id ? detailExample : session
    );
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
          statusText={signedInUser ? `Demo · ${signedInUser}` : "Mock data connected"}
        />

        {activePage === "dashboard" && (
          <Dashboard
            sessions={sessions}
            detailExample={detailExample}
            onNavigate={setActivePage}
            onOpenSession={openSession}
          />
        )}

        {activePage === "sessions" && (
          <SessionsWorkspace
            sessions={sessions}
            detailExample={detailExample}
            selectedSession={selectedSession}
            onSelectSession={selectSession}
            onNavigate={setActivePage}
            onToast={showToast}
          />
        )}

        {activePage === "risk-analytics" && (
          <RiskAnalytics sessions={sessions} />
        )}

        {activePage === "score-test" && (
          <ScoreSimulator scoreExample={scoreExample} onToast={showToast} />
        )}

        {activePage === "audit-log" && (
          <AuditLog
            sessions={sessions}
            detailExample={detailExample}
            onOpenSession={openSession}
          />
        )}

        {activePage === "api-system" && (
          <ApiSystem mockData={mockData} onToast={showToast} />
        )}

        {activePage === "api-keys" && <ApiKeys />}

        {toast && <div className={`toast toast-${toast.tone}`}>{toast.message}</div>}
      </main>
    </div>
  );
}
