import { useState } from 'react'
import type {
  HumanResponse,
  InteractiveAction,
  InteractiveActionsBlock,
} from '../../types'

const ACTION_LABEL: Record<InteractiveAction, string> = {
  approve: 'Approve',
  override: 'Override / Reject',
  request_documents: 'Request documents',
  submit_documents: 'Submit documents',
}

const STANDARD_DOCUMENTS = [
  'Itemized repair invoices / contractor estimate',
  'Medical bills, pharmacy receipts & discharge summary',
  'Police FIR / incident report',
  'Photographs of damaged property / scene',
]

interface Props {
  block: InteractiveActionsBlock
  onResume?: (response: HumanResponse) => void
  resuming?: boolean
}

export function InteractiveActionsBlockView({ block, onResume, resuming }: Props) {
  const [showApprove, setShowApprove] = useState(false)
  const [approveReason, setApproveReason] = useState('')

  const [showOverride, setShowOverride] = useState(false)
  const [overrideChoice, setOverrideChoice] = useState<'deny' | 'approve'>('deny')
  const [overrideReason, setOverrideReason] = useState('')
  const [proposedAmount, setProposedAmount] = useState('')

  const [showRequestDocs, setShowRequestDocs] = useState(false)
  const [selectedDocs, setSelectedDocs] = useState<string[]>([])
  const [docNotes, setDocNotes] = useState('')
  const [docsSubmittedLocally, setDocsSubmittedLocally] = useState(false)

  if (!block.is_pending) {
    return (
      <div className="block card interactive-actions-block">
        <h3>Actions</h3>
        <p className="empty-note">
          Review completed — action has been committed and recorded in the audit trail.
        </p>
      </div>
    )
  }

  const confirmFlow = block.resumes_at_node === 'commit_decision'
  const labels: Record<InteractiveAction, string> = confirmFlow
    ? {
        approve: 'Confirm approval',
        override: 'Override / Reject',
        request_documents: 'Request documents',
        submit_documents: 'Submit documents',
      }
    : ACTION_LABEL

  function handleActionClick(action: InteractiveAction) {
    if (action === 'approve') {
      setShowApprove((prev) => !prev)
      setShowOverride(false)
      setShowRequestDocs(false)
      return
    }
    if (action === 'override') {
      setShowOverride((prev) => !prev)
      setShowApprove(false)
      setShowRequestDocs(false)
      return
    }
    if (action === 'request_documents') {
      setShowRequestDocs((prev) => !prev)
      setShowApprove(false)
      setShowOverride(false)
      return
    }
    if (action === 'submit_documents') {
      handleSubmitDocuments()
    }
  }

  function submitApprove() {
    const reason = approveReason.trim()
    if (!reason) return
    onResume?.({
      action: 'approve',
      reason,
    })
    setShowApprove(false)
  }

  function submitOverride() {
    const reason = overrideReason.trim()
    if (!reason) return
    const parsedAmount =
      overrideChoice === 'deny'
        ? 0
        : proposedAmount === ''
        ? undefined
        : Math.round(Number(proposedAmount))

    onResume?.({
      action: 'override',
      override_outcome: overrideChoice,
      reason,
      ...(parsedAmount !== undefined ? { proposed_amount: parsedAmount } : {}),
    })
    setShowOverride(false)
  }

  function toggleDocSelection(doc: string) {
    setSelectedDocs((prev) =>
      prev.includes(doc) ? prev.filter((d) => d !== doc) : [...prev, doc],
    )
  }

  function submitRequestDocs() {
    const docs = [...selectedDocs]
    if (docNotes.trim() && !docs.includes(docNotes.trim())) {
      docs.push(docNotes.trim())
    }
    if (docs.length === 0) return

    onResume?.({
      action: 'request_documents',
      requested_documents: docs,
      reason: docNotes.trim() || `Requested: ${docs.join(', ')}`,
    })
    setShowRequestDocs(false)
  }

  function handleSubmitDocuments() {
    setDocsSubmittedLocally(true)
    onResume?.({
      action: 'submit_documents',
      reason: 'Requested documentation submitted by claimant.',
    })
  }

  const hasRequestedDocs =
    (block.requested_documents && block.requested_documents.length > 0) || docsSubmittedLocally

  return (
    <div className="block card interactive-actions-block pending">
      <h3>Actions needed</h3>
      <p className="muted">
        {confirmFlow
          ? 'Computed decision ready. Confirm to commit, override to reject/adjust, or request documents.'
          : `This claim is paused for human review (resumes at ${block.resumes_at_node}).`}
      </p>

      {/* Main Action Buttons */}
      <div className="action-buttons">
        {block.available_actions.map((action) => {
          const isApprove = action === 'approve'
          return (
            <button
              type="button"
              className={`btn ${isApprove ? '' : 'btn-secondary'}`}
              key={action}
              disabled={resuming}
              onClick={() => handleActionClick(action)}
            >
              {labels[action] ?? action}
            </button>
          )
        })}
      </div>

      {/* Approve Form */}
      {showApprove && (
        <div className="approve-form">
          <div className="form-section-title">
            <strong>{confirmFlow ? 'Confirm Claim Approval' : 'Approve Claim (Green Signal)'}</strong>
          </div>
          <p className="field-help">
            Enter adjuster review findings or justification to approve this claim:
          </p>

          <div className="field">
            <label htmlFor="approve_reason" className="field-label">
              Reason / approval notes (required)
            </label>
            <textarea
              id="approve_reason"
              rows={3}
              required
              value={approveReason}
              onChange={(e) => setApproveReason(e.target.value)}
              placeholder="Specify rationale for approval (e.g. verified policy coverage, reviewed line item damages, and evidence)..."
            />
          </div>

          <div className="form-actions-inline">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setShowApprove(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={resuming || !approveReason.trim()}
              onClick={submitApprove}
            >
              {resuming ? 'Approving…' : confirmFlow ? 'Confirm approval' : 'Approve claim'}
            </button>
          </div>
        </div>
      )}

      {/* Requested Documents Panel (when documents have been requested) */}
      {hasRequestedDocs && (
        <div className="requested-docs-panel">
          <div className="requested-docs-header">
            <strong>Requested documentation from claimant</strong>
            <span className="status-chip">Action required</span>
          </div>
          {block.requested_documents && block.requested_documents.length > 0 && (
            <ul className="requested-docs-list">
              {block.requested_documents.map((doc, i) => (
                <li key={i}>{doc}</li>
              ))}
            </ul>
          )}
          <div className="docs-submission-prompt">
            <p className="field-help">
              Upload or confirm receipt of the requested documents to continue review.
            </p>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={resuming || docsSubmittedLocally}
              onClick={handleSubmitDocuments}
            >
              {docsSubmittedLocally
                ? 'Documents submitted'
                : resuming
                ? 'Submitting…'
                : 'Submit requested documents'}
            </button>
          </div>
        </div>
      )}

      {/* Override / Reject Form */}
      {showOverride && (
        <div className="override-form">
          <div className="form-section-title">
            <strong>Override or Reject Claim</strong>
          </div>

          <div className="field">
            <span className="field-label">Resolution type</span>
            <div className="resolution-type-list">
              <label
                className={`resolution-type-item ${
                  overrideChoice === 'deny' ? 'checked deny-selected' : ''
                }`}
              >
                <input
                  type="checkbox"
                  checked={overrideChoice === 'deny'}
                  onChange={() => setOverrideChoice('deny')}
                />
                <div className="resolution-item-content">
                  <div className="resolution-item-title-row">
                    <span className="resolution-item-title">Reject / Deny claim</span>
                    <span className="resolution-badge deny-badge">Red signal</span>
                  </div>
                  <span className="resolution-item-desc">
                    Sets outcome to DENY with ₹0 payable amount and records formal denial grounds in the audit log.
                  </span>
                </div>
              </label>

              <label
                className={`resolution-type-item ${
                  overrideChoice === 'approve' ? 'checked approve-selected' : ''
                }`}
              >
                <input
                  type="checkbox"
                  checked={overrideChoice === 'approve'}
                  onChange={() => setOverrideChoice('approve')}
                />
                <div className="resolution-item-content">
                  <div className="resolution-item-title-row">
                    <span className="resolution-item-title">Approve with custom amount</span>
                    <span className="resolution-badge approve-badge">Green signal</span>
                  </div>
                  <span className="resolution-item-desc">
                    Adjusts payable amount with reviewer rationale and approves the claim.
                  </span>
                </div>
              </label>
            </div>
          </div>

          {overrideChoice === 'approve' && (
            <div className="field">
              <label htmlFor="override_amount" className="field-label">
                Approved amount (₹)
              </label>
              <input
                id="override_amount"
                type="number"
                min="0"
                step="1"
                value={proposedAmount}
                onChange={(event) => setProposedAmount(event.target.value)}
                placeholder="e.g. 25000"
                required
              />
            </div>
          )}

          <div className="field">
            <label htmlFor="override_reason" className="field-label">
              Reason for decision (required)
            </label>
            <textarea
              id="override_reason"
              rows={3}
              required
              value={overrideReason}
              onChange={(event) => setOverrideReason(event.target.value)}
              placeholder={
                overrideChoice === 'deny'
                  ? 'Specify the grounds for rejection (e.g. policy exclusion clause, non-covered cause, or insufficient evidence)...'
                  : 'Explain why the computed amount was adjusted...'
              }
            />
          </div>

          <div className="form-actions-inline">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setShowOverride(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={
                resuming ||
                !overrideReason.trim() ||
                (overrideChoice === 'approve' &&
                  proposedAmount !== '' &&
                  (!Number.isInteger(Number(proposedAmount)) || Number(proposedAmount) < 0))
              }
              onClick={submitOverride}
            >
              {resuming
                ? 'Submitting…'
                : overrideChoice === 'deny'
                ? 'Confirm rejection'
                : 'Confirm custom approval'}
            </button>
          </div>
        </div>
      )}

      {/* Request Documents Form */}
      {showRequestDocs && (
        <div className="request-docs-form">
          <div className="form-section-title">
            <strong>Request Additional Documentation</strong>
          </div>
          <p className="field-help">
            Select required evidence or specify additional information needed from the claimant:
          </p>

          <div className="docs-checklist">
            {STANDARD_DOCUMENTS.map((doc) => {
              const isChecked = selectedDocs.includes(doc)
              return (
                <label key={doc} className={`doc-check-item ${isChecked ? 'checked' : ''}`}>
                  <input
                    type="checkbox"
                    checked={isChecked}
                    onChange={() => toggleDocSelection(doc)}
                  />
                  <span>{doc}</span>
                </label>
              )
            })}
          </div>

          <div className="field">
            <label htmlFor="doc_notes" className="field-label">
              Specific instructions to claimant
            </label>
            <textarea
              id="doc_notes"
              rows={2}
              value={docNotes}
              onChange={(e) => setDocNotes(e.target.value)}
              placeholder="e.g. Please provide itemized invoices showing parts and labour breakdown..."
            />
          </div>

          <div className="form-actions-inline">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setShowRequestDocs(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={resuming || (selectedDocs.length === 0 && !docNotes.trim())}
              onClick={submitRequestDocs}
            >
              {resuming ? 'Sending request…' : 'Send document request'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
