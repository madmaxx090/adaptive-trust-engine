export default function StatCard({ label, value, meta, tone = "default", onClick }) {
  return (
    <button className={`stat-card tone-${tone}`} type="button" onClick={onClick}>
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value}</strong>
      <span className="stat-meta">{meta}</span>
    </button>
  );
}
