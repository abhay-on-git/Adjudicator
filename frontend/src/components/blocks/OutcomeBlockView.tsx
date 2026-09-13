import { formatInr, formatOutcome } from '../../lib/format'
import type { OutcomeBlock } from '../../types'

export function OutcomeBlockView({ block }: { block: OutcomeBlock }) {
  const pct = Math.round(block.confidence * 100)
  return (
    <div className={`block card outcome-block outcome-${block.outcome}`}>
      <div className="outcome-header">
        <span className="outcome-label">{formatOutcome(block.outcome)}</span>
        <span className="outcome-amount">{formatInr(block.amount)}</span>
      </div>
      <div className="confidence">
        <span>Confidence {pct}%</span>
        <div className="confidence-track" aria-hidden>
          <div className="confidence-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>
      {block.escalation_reason && (
        <div className="escalation-reason">{block.escalation_reason}</div>
      )}
      {block.narrative && <p className="outcome-narrative">{block.narrative}</p>}
    </div>
  )
}
