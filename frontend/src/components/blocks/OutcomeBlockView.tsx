import type { OutcomeBlock } from '../../types'

const OUTCOME_LABEL: Record<OutcomeBlock['outcome'], string> = {
  approve: 'Approved',
  deny: 'Denied',
  partial: 'Partially Approved',
  escalate: 'Escalated for Review',
}

export function OutcomeBlockView({ block }: { block: OutcomeBlock }) {
  return (
    <div className={`block outcome-block outcome-${block.outcome}`}>
      <div className="outcome-header">
        <span className="outcome-label">{OUTCOME_LABEL[block.outcome]}</span>
        <span className="outcome-amount">₹{block.amount.toLocaleString('en-IN')}</span>
      </div>
      <div className="outcome-confidence">Confidence: {(block.confidence * 100).toFixed(0)}%</div>
      {block.escalation_reason && (
        <div className="escalation-reason">Escalation reason: {block.escalation_reason}</div>
      )}
      {block.narrative && <p className="outcome-narrative">{block.narrative}</p>}
    </div>
  )
}
