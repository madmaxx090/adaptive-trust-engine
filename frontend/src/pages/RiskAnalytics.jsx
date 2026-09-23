import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import RiskChart from "../components/RiskChart";
import RiskBadge from "../components/RiskBadge";

export default function RiskAnalytics({ sessions }) {
  const low = sessions.filter((s) => s.risk_tier === "low").length;
  const medium = sessions.filter((s) => s.risk_tier === "medium").length;
  const high = sessions.filter((s) => s.risk_tier === "high").length;
  const distribution = [
    { name: "Low", value: low, fill: "#3eb879" },
    { name: "Medium", value: medium, fill: "#e7aa38" },
    { name: "High", value: high, fill: "#df5e6d" },
  ];
  const bars = sessions.map((s) => ({ session: s.session_id.replace("sess_", "#"), score: s.risk_score, tier: s.risk_tier }));
  const suspiciousPercent = Math.round(((medium + high) / sessions.length) * 100);
  const maxRisk = Math.max(...sessions.map((s) => s.risk_score));
  const avgRisk = (sessions.reduce((sum, s) => sum + s.risk_score, 0) / sessions.length).toFixed(1);
  const barColor = (tier) => tier === "high" ? "#df5e6d" : tier === "medium" ? "#e7aa38" : "#3eb879";

  return (
    <section className="page-stack">
      <div className="analytics-kpis">
        <div className="mini-kpi"><span>Average risk</span><strong>{avgRisk}</strong></div>
        <div className="mini-kpi"><span>Highest score</span><strong>{maxRisk}</strong></div>
        <div className="mini-kpi"><span>Suspicious sessions</span><strong>{suspiciousPercent}%</strong></div>
        <div className="mini-kpi"><span>High-risk sessions</span><strong>{high}</strong></div>
      </div>

      <div className="dashboard-grid">
        <div className="panel"><div className="panel-heading"><div><h2>Risk over time</h2><p>Session risk trend</p></div></div><RiskChart sessions={sessions} height={330} /></div>
        <div className="panel">
          <div className="panel-heading"><div><h2>Tier composition</h2><p>Low, medium and high-risk share</p></div></div>
          <div className="analytics-pie-wrap">
            <div className="analytics-pie"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={distribution} dataKey="value" innerRadius={58} outerRadius={86} paddingAngle={4}>{distribution.map((entry) => <Cell key={entry.name} fill={entry.fill} />)}</Pie><Tooltip /></PieChart></ResponsiveContainer></div>
            <div className="analytics-legend">{distribution.map((item) => <div key={item.name}><RiskBadge tier={item.name.toLowerCase()} /><strong>{item.value}</strong></div>)}</div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-heading"><div><h2>Per-session score comparison</h2><p>Higher bars indicate higher authentication risk</p></div></div>
        <div className="chart-host" style={{ height: 330 }}><ResponsiveContainer width="100%" height="100%"><BarChart data={bars} margin={{ top: 10, right: 10, left: -16, bottom: 0 }}><CartesianGrid strokeDasharray="4 4" vertical={false} stroke="#e6edf4" /><XAxis dataKey="session" axisLine={false} tickLine={false} /><YAxis domain={[0, 100]} axisLine={false} tickLine={false} /><Tooltip /><Bar dataKey="score" radius={[8, 8, 0, 0]}>{bars.map((entry) => <Cell key={entry.session} fill={barColor(entry.tier)} />)}</Bar></BarChart></ResponsiveContainer></div>
      </div>
    </section>
  );
}
