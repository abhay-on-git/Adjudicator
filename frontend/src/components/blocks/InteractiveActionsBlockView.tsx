import { useState } from 'react'
import type {
  HumanResponse,
  InteractiveAction,
  InteractiveActionsBlock,
} from '../../types'

const ACTION_LABEL: Record<InteractiveAction, string> = {
  approve: 'Approve',
  override: 'Override',
  request_documents: 'Request documents',
}

interface Props {
  block: InteractiveActionsBlock
  onResume?: (response: HumanResponse) => void
  resuming?: boolean
}

export function InteractiveActionsBlockView({ block, onResume, resuming }: Props) {
  const [showOverride, setShowOverride] = useState(false)
  const [overrideReason, setOverrideReason] = useState('')
  const [proposedAmount, setProposedAmount] = useState('')

  if (!block.is_pending) {
    return (
      <div className="block card interactive-actions-block">
        <h3>Actions</h3>
        <p className="empty-note">Nothing pending — this decision is already final.</p>
      </div>
    )
  }
  const confirmFlow = block.resumes_at_node === 'commit_decision'
  const labels: Record<InteractiveAction, string> = confirmFlow
    ? { approve: 'Confirm', override: 'Override', request_documents: 'Request documents' }
    : ACTION_LABEL

  function handleAction(action: InteractiveAction) {
    if (confirmFlow && action === 'override') {
      setShowOverride(true)
      return
    }
    onResume?.(action)
  }

  function submitOverride() {
    const reason = overrideReason.trim()
    if (!reason) return
    const parsedAmount = proposedAmount === '' ? undefined : Number(proposedAmount)
    onResume?.({
      action: 'override',
      reason,
      ...(parsedAmount === undefined ? {} : { proposed_amount: parsedAmount }),
    })
  }

  return (
    <div className="block card interactive-actions-block pending">
      <h3>Actions needed</h3>
      <p className="muted">
        {confirmFlow
          ? 'Paused for confirmation (resumes at commit_decision).'
          : `This claim is paused for human review (resumes at ${block.resumes_at_node}).`}
      </p>
      <div className="action-buttons">
        {block.available_actions.map((action) => (
          <button
            type="button"
            className="btn btn-secondary"
            key={action}
            disabled={resuming}
            onClick={() => handleAction(action)}
          >
            {labels[action]}
          </button>
        ))}
      </div>
      {showOverride && (
        <div className="override-form">
          <label className="field">
            <span className="field-label">Override reason (required)</span>
            <textarea
              rows={3}
              required
              value={overrideReason}
              onChange={(event) => setOverrideReason(event.target.value)}
              placeholder="Explain why the computed decision should not be committed."
            />
          </label>
          <label className="field">
            <span className="field-label">Proposed amount (optional, audit note only)</span>
            <input
              type="number"
              min="0"
              step="1"
              value={proposedAmount}
              onChange={(event) => setProposedAmount(event.target.value)}
              placeholder="e.g. 60000"
            />
          </label>
          <p className="muted">
            This figure is recorded for review only. It never changes Decision.amount and
            is never sent to compute_payout.
          </p>
          <button
            type="button"
            className="btn"
            disabled={
              resuming ||
              !overrideReason.trim() ||
              (proposedAmount !== '' &&
                (!Number.isInteger(Number(proposedAmount)) || Number(proposedAmount) < 0))
            }
            onClick={submitOverride}
          >
            {resuming ? 'Submitting…' : 'Submit override'}
          </button>
        </div>
      )}
    </div>
  )
}
