import { formatRiskFlag } from '../../lib/format'
import type { RiskSignalBlock } from '../../types'

export function RiskSignalBlockView({ block }: { block: RiskSignalBlock }) {
  return (
    <div className={`block card risk-signal-block risk-${block.severity}`}>
      <h3>Risk signals</h3>
      <div className="risk-severity">
        <span className="severity-chip">{block.severity}</span>
      </div>
      {block.flags.length > 0 ? (
        <ul className="risk-flags">
          {block.flags.map((flag) => (
            <li key={flag}>{formatRiskFlag(flag)}</li>
          ))}
        </ul>
      ) : (
        <p className="empty-note">No risk flags raised.</p>
      )}
    </div>
  )
}
