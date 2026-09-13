const POLICY_LABELS: Record<string, string> = {
  'POL-HOME-01': 'Home',
  'POL-HEALTH-01': 'Health',
  'POL-MOTOR-01': 'Motor',
  'POL-TRAVEL-01': 'Travel',
}

const PERIL_LABELS: Record<string, string> = {
  fire: 'Fire',
  water_damage: 'Water damage',
  theft: 'Theft',
  hospitalization: 'Hospitalization',
  accidental_injury: 'Accidental injury',
  motor_accident: 'Motor accident',
  motor_theft: 'Motor theft',
  trip_cancellation: 'Trip cancellation',
  baggage_loss: 'Baggage loss',
  medical_abroad: 'Medical abroad',
  other: 'Other',
}

const RISK_FLAG_LABELS: Record<string, string> = {
  filed_before_loss_date: 'Filed before date of loss',
  injection_attempt_detected: 'Prompt-injection patterns in narrative',
  possible_duplicate_claim: 'Possible duplicate claim',
  high_value_claim_missing_documentation: 'High-value claim without documents',
  suspiciously_round_amount: 'Suspiciously round amount',
}

const EVENT_LABELS: Record<string, string> = {
  intake_complete: 'Intake complete',
  extraction_complete: 'Facts extracted',
  extraction_retry: 'Extraction retry',
  routing_complete: 'Routed',
  retrieval_complete: 'Policy found',
  retrieval_empty: 'No governing policy',
  eligibility_complete: 'Eligibility computed',
  evidence_reconciled: 'Decision evidence verified',
  evidence_reconciliation_failed: 'Decision evidence unavailable',
  risk_complete: 'Risk checked',
  decision_composed: 'Decision composed',
  escalated: 'Escalated',
  escalation_resolved: 'Review resolved',
  llm_retry: 'Model retry',
  llm_unavailable: 'Model unavailable',
  explanation_complete: 'Explanation written',
  groundedness_violation: 'Groundedness violation',
  context_budget_drop: 'Context budget drop',
  ui_composed: 'Dossier composed',
  decision_confirmed: 'Decision confirmed',
  decision_overridden: 'Decision overridden',
  decision_documents_requested: 'Documents requested',
  review_flagged: 'Manual review flagged',
}

const OUTCOME_LABELS: Record<string, string> = {
  approve: 'Approved',
  deny: 'Denied',
  partial: 'Partially approved',
  escalate: 'Escalated for review',
}

export function policyLabel(policyId: string): string {
  return POLICY_LABELS[policyId] ?? policyId
}

export function policyOptionLabel(policyId: string): string {
  const name = POLICY_LABELS[policyId]
  return name ? `${name} · ${policyId}` : policyId
}

export function formatInr(amount: number): string {
  return `₹${amount.toLocaleString('en-IN')}`
}

export function formatPeril(peril: string): string {
  return PERIL_LABELS[peril] ?? peril.replaceAll('_', ' ')
}

export function formatRiskFlag(flag: string): string {
  return RISK_FLAG_LABELS[flag] ?? flag.replaceAll('_', ' ')
}

export function formatEventType(eventType: string): string {
  return EVENT_LABELS[eventType] ?? eventType.replaceAll('_', ' ')
}

export function formatOutcome(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome
}

export function formatDate(value: string): string {
  if (!value) return '—'
  const parsed = new Date(`${value}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

export function formatTimestamp(iso: string): string {
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return parsed.toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function humanizeToken(value: string): string {
  return value.replaceAll('_', ' ')
}
