export default function RiskBadge({ tier }) {
  return <span className={`risk risk-${tier}`}>{String(tier).toUpperCase()}</span>;
}
