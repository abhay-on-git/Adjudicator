import type { RiskSignalBlock } from '../../types'

export function RiskSignalBlockView({ block }: { block: RiskSignalBlock }) {
  return (
    <div className={`block risk-signal-block risk-${block.severity}`}>
      <h3>Risk Signals</h3>
      <div className="risk-severity">Severity: {block.severity}</div>
      {block.flags.length > 0 ? (
        <ul className="risk-flags">
          {block.flags.map((flag) => (
            <li key={flag}>{flag}</li>
          ))}
        </ul>
      ) : (
        <p className="empty-note">No risk flags raised.</p>
      )}
    </div>
  )
}
