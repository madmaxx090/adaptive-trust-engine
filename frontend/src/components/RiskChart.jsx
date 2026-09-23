import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function RiskChart({ sessions, height = 300 }) {
  const data = sessions.map((session) => ({
    session: session.session_id.replace("sess_", "#"),
    risk: session.risk_score,
  }));

  return (
    <div className="chart-host" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 12, right: 16, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="4 4" vertical={false} stroke="#e6edf4" />
          <XAxis dataKey="session" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: "#76889b" }} />
          <YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: "#76889b" }} />
          <Tooltip contentStyle={{ borderRadius: 10, border: "1px solid #dfe8ef", boxShadow: "0 8px 30px rgba(14,42,68,.08)" }} />
          <Line type="monotone" dataKey="risk" stroke="#1f78c1" strokeWidth={3} dot={{ r: 4, fill: "#fff", stroke: "#1f78c1", strokeWidth: 2 }} activeDot={{ r: 6 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
