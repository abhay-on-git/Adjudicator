import { useEffect, useRef, useState } from 'react'
import { resumeClaim, streamSSE } from '../api'
import type { AuditLogEntry, Decision, DecisionUISpec, InteractiveAction } from '../types'
import { BlockRenderer } from './BlockRenderer'

type Status = 'streaming' | 'escalated' | 'done' | 'error'

interface Props {
  initialResponse: Response
  onReset: () => void
}

export function ClaimStreamView({ initialResponse, onReset }: Props) {
  const [claimId, setClaimId] = useState<string | null>(null)
  const [nodeLog, setNodeLog] = useState<string[]>([])
  const [auditLog, setAuditLog] = useState<AuditLogEntry[]>([])
  const [status, setStatus] = useState<Status>('streaming')
  const [escalationReason, setEscalationReason] = useState<string | null>(null)
  const [decision, setDecision] = useState<Decision | null>(null)
  const [uiSpec, setUiSpec] = useState<DecisionUISpec | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [resuming, setResuming] = useState(false)
  const [resumeText, setResumeText] = useState('')

  // Guards against React StrictMode's double-invoke of effects in dev
  // consuming the same one-shot Response body twice.
  const consumedResponses = useRef(new WeakSet<Response>())

  useEffect(() => {
    if (consumedResponses.current.has(initialResponse)) return
    consumedResponses.current.add(initialResponse)
    consume(initialResponse)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialResponse])

  async function consume(response: Response) {
    try {
      for await (const evt of streamSSE(response)) {
        if (evt.event === 'node_complete') {
          setClaimId(evt.data.claim_id)
          setNodeLog((prev) => [...prev, evt.data.node])
          if (evt.data.update.audit_log) {
            setAuditLog((prev) => [...prev, ...evt.data.update.audit_log!])
          }
        } else if (evt.event === 'escalated') {
          setClaimId(evt.data.claim_id)
          setStatus('escalated')
          setEscalationReason(evt.data.reason)
        } else if (evt.event === 'done') {
          setClaimId(evt.data.claim_id)
          setStatus('done')
          setDecision(evt.data.decision)
          setUiSpec(evt.data.ui_spec)
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

  function handleActionButton(action: InteractiveAction) {
    void handleResume(action)
  }

  function handleFreeTextResume() {
    void handleResume(resumeText || 'reviewed manually, no notes')
  }

  return (
    <div className="claim-stream-view">
      <div className="stream-header">
        <h2>{claimId ?? 'Submitting…'}</h2>
        <button onClick={onReset}>New Claim</button>
      </div>

      <div className="node-log">
        <h3>Graph Progress</h3>
        <ol>
          {nodeLog.map((node, i) => (
            <li key={i}>{node}</li>
          ))}
          {status === 'streaming' && <li className="pending">…</li>}
        </ol>
        <p className="hop-count">
          {nodeLog.length} node{nodeLog.length === 1 ? '' : 's'} executed
          {status !== 'streaming' && ' — fewer hops than a multi-peril or degraded claim would take.'}
        </p>
      </div>

      {status === 'escalated' && (
        <div className="block escalation-panel">
          <h3>Escalated for Human Review</h3>
          <p>{escalationReason}</p>
          <label>
            Resolution notes
            <textarea rows={3} value={resumeText} onChange={(e) => setResumeText(e.target.value)} />
          </label>
          <button disabled={resuming} onClick={handleFreeTextResume}>
            Resume as Adjuster
          </button>
        </div>
      )}

      {status === 'error' && (
        <div className="block error-panel">
          <h3>Something went wrong</h3>
          <p>{error}</p>
        </div>
      )}

      {status === 'done' && uiSpec && (
        <div className="blocks">
          {uiSpec.blocks.map((block, i) => (
            <BlockRenderer key={i} block={block} onResume={handleActionButton} resuming={resuming} />
          ))}
        </div>
      )}

      {status === 'done' && !uiSpec && (
        <div className="block">
          <p>
            Resolved without an automated decision (escalated before the graph reached a decision
            {decision ? '' : ' — a human handled this claim outside the automated flow'}).
          </p>
        </div>
      )}

      <details className="audit-log">
        <summary>Audit Log ({auditLog.length})</summary>
        <table>
          <thead>
            <tr>
              <th>Node</th>
              <th>Event</th>
              <th>Detail</th>
              <th>Timestamp</th>
            </tr>
          </thead>
          <tbody>
            {auditLog.map((entry, i) => (
              <tr key={i}>
                <td>{entry.node}</td>
                <td>{entry.event_type}</td>
                <td>{entry.detail}</td>
                <td>{entry.timestamp}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  )
}
