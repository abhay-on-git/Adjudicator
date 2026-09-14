import { useEffect, useRef, useState } from 'react'
import { resumeClaim, streamSSE } from '../api'
import { formatDate, policyLabel } from '../lib/format'
import type { PipelineStatus } from '../lib/pipeline'
import type {
  AuditLogEntry,
  ClaimSubmission,
  DecisionUISpec,
  HumanResponse,
  NodeUpdate,
} from '../types'
import { AuditTimeline } from './AuditTimeline'
import { BlockRenderer } from './BlockRenderer'
import { PipelineStepper } from './PipelineStepper'
import { ProgressiveSections } from './ProgressiveSections'

interface Props {
  initialResponse: Response
  submission: ClaimSubmission
  onReset: () => void
  onBusyChange?: (busy: boolean) => void
}

export function ClaimStreamView({ initialResponse, submission, onReset, onBusyChange }: Props) {
  const [claimId, setClaimId] = useState<string | null>(submission.claim_id ?? null)
  const [nodeLog, setNodeLog] = useState<string[]>([])
  const [nodeUpdates, setNodeUpdates] = useState<Record<string, NodeUpdate>>({})
  const [auditLog, setAuditLog] = useState<AuditLogEntry[]>([])
  const [status, setStatus] = useState<PipelineStatus>('streaming')
  const [escalationReason, setEscalationReason] = useState<string | null>(null)
  const [uiSpec, setUiSpec] = useState<DecisionUISpec | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resuming, setResuming] = useState(false)
  const [resumeText, setResumeText] = useState('')

  const consumedResponses = useRef(new WeakSet<Response>())

  useEffect(() => {
    if (consumedResponses.current.has(initialResponse)) return
    consumedResponses.current.add(initialResponse)
    void consume(initialResponse)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialResponse])

  useEffect(() => {
    onBusyChange?.(status === 'streaming' || resuming)
  }, [status, resuming, onBusyChange])

  async function consume(response: Response) {
    try {
      for await (const evt of streamSSE(response)) {
        if (evt.event === 'node_complete') {
          setClaimId(evt.data.claim_id)
          setNodeLog((prev) => [...prev, evt.data.node])
          setNodeUpdates((prev) => ({ ...prev, [evt.data.node]: evt.data.update }))
          if (evt.data.update.audit_log) {
            setAuditLog((prev) => [...prev, ...evt.data.update.audit_log!])
          }
          if (evt.data.update.ui_spec) {
            setUiSpec(evt.data.update.ui_spec)
          }
        } else if (evt.event === 'escalated') {
          setClaimId(evt.data.claim_id)
          setStatus('escalated')
          setEscalationReason(evt.data.reason)
        } else if (evt.event === 'awaiting_confirmation') {
          setClaimId(evt.data.claim_id)
          setStatus('awaiting_confirmation')
        } else if (evt.event === 'done') {
          setClaimId(evt.data.claim_id)
          setStatus('done')
          if (evt.data.ui_spec) setUiSpec(evt.data.ui_spec)
        }
      }
    } catch (err) {
      setStatus('error')
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleResume(humanResponse: unknown) {
    if (!claimId) return
    setResuming(true)
    setError(null)
    try {
      const response = await resumeClaim(claimId, humanResponse)
      setStatus('streaming')
      await consume(response)
    } catch (err) {
      setStatus('error')
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setResuming(false)
    }
  }

  function handleActionButton(response: HumanResponse) {
    void handleResume(response)
  }

  function handleFreeTextResume() {
    void handleResume(resumeText || 'reviewed manually, no notes')
  }

  const fastPath = nodeUpdates.router?.routing?.fast_path
  const showDossier = Boolean(uiSpec)
  const truncatedNarrative =
    submission.narrative_text.length > 180
      ? `${submission.narrative_text.slice(0, 180).trim()}…`
      : submission.narrative_text

  return (
    <div className="claim-stream-view">
      <div className="case-header card">
        <div>
          <h2 className="mono">{claimId ?? 'Submitting…'}</h2>
          <div className="case-meta">
            <span>
              {policyLabel(submission.policy_id)} · {submission.policy_id}
            </span>
            <span>{submission.claimant_name}</span>
            <span>{submission.claimant_city}</span>
            <span>Start {formatDate(submission.policy_start_date)}</span>
            <span>Filed {formatDate(submission.filed_date)}</span>
          </div>
          {truncatedNarrative && <p className="case-narrative">{truncatedNarrative}</p>}
        </div>
      </div>

      <div className="workspace">
        <PipelineStepper completed={nodeLog} status={status} fastPath={fastPath} />

        <div className="workspace-feed">
          {status === 'error' && (
            <div className="card error-panel">
              <h3>Something went wrong</h3>
              <p>{error}</p>
              <button type="button" className="btn btn-ghost" onClick={onReset}>
                Back
              </button>
            </div>
          )}

          {status === 'awaiting_confirmation' && (
            <div className="card confirmation-panel">
              <h3>Confirm this decision</h3>
              <p>
                The payable amount is already computed. Confirm it, override to human review, or
                request documents — none of those actions lets the model change the figure.
              </p>
            </div>
          )}

          {status === 'escalated' && (
            <div className="card escalation-panel">
              <h3>Escalated for human review</h3>
              <p>{escalationReason}</p>
              {escalationReason?.toLowerCase().includes('missing') && (
                <div className="escalation-missing-hint">
                  <p>
                    Essential facts (such as the incident date or claimed items) were missing from the
                    narrative. You can supply resolution notes below to close this review, or return to
                    edit and supply the missing details to adjudicate the claim.
                  </p>
                  <button type="button" className="btn btn-secondary btn-sm" onClick={onReset}>
                    Edit claim details & re-adjudicate
                  </button>
                </div>
              )}
              {/* Only show fallback free-text adjuster input if no dossier / UI spec is available */}
              {!uiSpec && (
                <>
                  <label className="field">
                    <span className="field-label">Resolution notes</span>
                    <textarea
                      rows={3}
                      value={resumeText}
                      onChange={(e) => setResumeText(e.target.value)}
                    />
                  </label>
                  <button type="button" className="btn" disabled={resuming} onClick={handleFreeTextResume}>
                    {resuming ? 'Resuming…' : 'Resume as adjuster'}
                  </button>
                </>
              )}
            </div>
          )}

          {showDossier && uiSpec ? (
            <div className="blocks">
              {(() => {
                // Prioritize pending interactive actions block at the very top of the dossier
                const pendingActions = uiSpec.blocks.filter(
                  (b) => b.type === 'interactive_actions' && (b as any).is_pending,
                )
                const otherBlocks = uiSpec.blocks.filter(
                  (b) => !(b.type === 'interactive_actions' && (b as any).is_pending),
                )
                return [...pendingActions, ...otherBlocks].map((block, i) => (
                  <BlockRenderer
                    key={`${block.type}-${i}`}
                    block={block}
                    onResume={handleActionButton}
                    resuming={resuming}
                  />
                ))
              })()}
            </div>
          ) : (
            <ProgressiveSections updates={nodeUpdates} status={status} />
          )}

          {status === 'done' && !uiSpec && (
            <div className="card block">
              <p>
                Resolved without an automated decision (escalated before the graph reached a
                decision).
              </p>
            </div>
          )}

          <AuditTimeline entries={auditLog} />
        </div>
      </div>
    </div>
  )
}
