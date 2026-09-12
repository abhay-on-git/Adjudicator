/**
 * Mirrors agent-service/graph/schemas.py's UI block models and SSE event
 * shapes exactly (see routes.py's `_json_safe` for how Pydantic models
 * become these plain JSON shapes, and DESIGN.md's "HTTP contract" note).
 *
 * There is no code generation here — this file is hand-kept in sync with
 * schemas.py. If it ever drifts, `UnknownBlock`'s fallback rendering (see
 * BlockRenderer.tsx) is exactly the safety net for that: an unrecognized
 * `type` renders as raw JSON instead of crashing the page.
 */

export type Outcome = 'approve' | 'deny' | 'partial' | 'escalate'
export type LineItemVerdict = 'allowed' | 'reduced' | 'excluded'
export type RiskSeverity = 'none' | 'low' | 'medium' | 'high'
export type InteractiveAction = 'approve' | 'override' | 'request_documents'
export type ClaimStatus = 'pending' | 'in_progress' | 'escalated' | 'done'

export interface Decision {
  outcome: Outcome
  amount: number
  confidence: number
  escalation_reason: string | null
}

export interface EvidenceClause {
  clause_id: string
  policy_id: string
  title: string
  text: string
}

export interface LineItemBreakdownRow {
  description: string
  claimed_amount: number
  allowed_amount: number
  verdict: LineItemVerdict
  governing_clause_ids: string[]
  reason: string
}

export interface OutcomeBlock {
  type: 'outcome'
  outcome: Outcome
  amount: number
  confidence: number
  escalation_reason: string | null
  narrative: string
}

export interface ClauseEvidenceBlock {
  type: 'clause_evidence'
  clauses: EvidenceClause[]
}

export interface LineItemBreakdownBlock {
  type: 'line_item_breakdown'
  rows: LineItemBreakdownRow[]
  deductible_applied: number
}

export interface InteractiveActionsBlock {
  type: 'interactive_actions'
  claim_id: string
  thread_id: string
  available_actions: InteractiveAction[]
  resumes_at_node: string | null
  is_pending: boolean
}

export interface RiskSignalBlock {
  type: 'risk_signal'
  severity: RiskSeverity
  flags: string[]
}

/** Any block whose `type` this frontend doesn't (yet) recognize — the
 * "unrecognized block type" fallback the spec asks for, exercised for real
 * whenever the backend's schema drifts ahead of this file. */
export interface UnknownBlock {
  type: string
  [key: string]: unknown
}

export type Block =
  | OutcomeBlock
  | ClauseEvidenceBlock
  | LineItemBreakdownBlock
  | InteractiveActionsBlock
  | RiskSignalBlock
  | UnknownBlock

export interface DecisionUISpec {
  claim_id: string
  blocks: Block[]
}

export interface AuditLogEntry {
  event_type: string
  node: string
  detail: string
  timestamp: string
}

/** One SSE `node_complete` event's `data` payload. `update` is whatever
 * that particular node returned from its LangGraph function — see each
 * node module in agent-service/graph/nodes/ for the exact shape per node;
 * the only key every node update MAY contain is `audit_log`. */
export interface NodeCompleteEvent {
  claim_id: string
  node: string
  update: {
    audit_log?: AuditLogEntry[]
    decision?: Decision
    ui_spec?: DecisionUISpec
    [key: string]: unknown
  }
}

export interface EscalatedEvent {
  claim_id: string
  interrupt_id: string
  reason: string
  decision_so_far: Decision | null
}

export interface DoneEvent {
  claim_id: string
  decision: Decision | null
  ui_spec: DecisionUISpec | null
}

export type StreamEvent =
  | { event: 'node_complete'; data: NodeCompleteEvent }
  | { event: 'escalated'; data: EscalatedEvent }
  | { event: 'done'; data: DoneEvent }

export interface ClaimSubmission {
  claim_id?: string
  policy_id: string
  policy_start_date: string
  filed_date: string
  claimant_name: string
  claimant_gender: string
  claimant_city: string
  narrative_text: string
}

export interface ClaimDetail {
  claim_id: string
  policy_id: string
  policy_start_date: string
  filed_date: string
  claimant_name: string
  claimant_gender: string
  claimant_city: string
  narrative_text: string
  status: ClaimStatus
  created_at: string
  updated_at: string
  decision: (Decision & { ui_spec: DecisionUISpec | null }) | null
  audit_events: AuditLogEntry[]
}
