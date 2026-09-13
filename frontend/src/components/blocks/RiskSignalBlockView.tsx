import { formatRiskFlag } from '../../lib/format'
import type { RiskSeverity, RiskSignalBlock } from '../../types'

const SEVERITY_LABEL: Record<RiskSeverity, string> = {
  none: 'None',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
}

export function RiskSignalBlockView({ block }: { block: RiskSignalBlock }) {
  const duplicates = block.duplicate_claim_ids ?? []
  const flagged = block.flags.length > 0

  return (
    <div className={`block card risk-signal-block risk-${block.severity}`}>
      <h3>Risk / anomalies</h3>
      <div className="risk-severity">
        <span className="muted">Severity</span>{' '}
        <span className="severity-chip">{SEVERITY_LABEL[block.severity]}</span>
      </div>
      {flagged ? (
        <ul className="risk-flags">
          {block.flags.map((flag) => (
            <li key={flag}>
              {formatRiskFlag(flag)}
              {flag === 'possible_duplicate_claim' && duplicates.length > 0
                ? ` (${duplicates.join(', ')})`
                : ''}
            </li>
          ))}
        </ul>
      ) : (
        <p className="empty-note">No anomalies detected.</p>
      )}
    </div>
  )
}
