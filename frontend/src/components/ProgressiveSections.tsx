import { formatDate, formatInr, formatOutcome, formatPeril, formatRiskFlag, humanizeToken } from '../lib/format'
import type { PipelineStatus } from '../lib/pipeline'
import type { NodeUpdate } from '../types'
import { WorkspaceSection, type SectionState } from './WorkspaceSection'

interface Props {
  updates: Record<string, NodeUpdate>
  status: PipelineStatus
}

function sectionState(ready: boolean, liveNode: boolean): SectionState {
  if (ready) return 'ready'
  if (liveNode) return 'live'
  return 'pending'
}

export function ProgressiveSections({ updates, status }: Props) {
  const intake = updates.intake_normalize
  const facts = updates.extraction?.claim_facts
  const routing = updates.router?.routing
  const retrieval = updates.policy_retrieval
  const eligibility = updates.eligibility_evaluation?.eligibility_result
  const risk = updates.risk_anomaly?.risk_result
  const decision = updates.decision_composition?.decision
  const explanation = updates.explanation?.explanation_text
  const streaming = status === 'streaming'

  const flags = Array.isArray(intake?.injection_flags) ? intake.injection_flags : []
  const clauses = retrieval?.retrieved_clauses ?? []
  const coverageHit = retrieval?.retrieval_had_coverage_hit

  return (
    <>
      <WorkspaceSection
        title="Intake"
        state={sectionState(Boolean(intake), streaming && !intake)}
        hint="Normalizing the narrative."
      >
        <p>Narrative ready for extraction.</p>
        {flags.length > 0 && (
          <div className="kv" style={{ marginTop: 8 }}>
            {flags.map((flag) => (
              <span className="chip" key={flag}>
                {humanizeToken(String(flag))}
              </span>
            ))}
          </div>
        )}
      </WorkspaceSection>

      <WorkspaceSection
        title="Extracted facts"
        state={sectionState(Boolean(facts), streaming && Boolean(intake) && !facts)}
        hint="Reading the loss narrative."
      >
        {facts && (
          <>
            <p className="muted">
              Date of loss {facts.date_of_loss ? formatDate(facts.date_of_loss) : 'not stated'} ·{' '}
              {facts.line_items.length} line item{facts.line_items.length === 1 ? '' : 's'}
              {facts.cause_ambiguous ? ' · cause ambiguous' : ''}
            </p>
            <div className="kv" style={{ marginTop: 8 }}>
              {facts.perils.map((peril) => (
                <span className="chip" key={peril}>
                  {formatPeril(peril)}
                </span>
              ))}
            </div>
            {facts.narrative_summary && (
              <p className="muted" style={{ marginTop: 8 }}>
                {facts.narrative_summary}
              </p>
            )}
          </>
        )}
      </WorkspaceSection>

      <WorkspaceSection
        title="Route"
        state={sectionState(Boolean(routing), streaming && Boolean(facts) && !routing)}
        hint="Checking completeness and complexity."
      >
        {routing && (
          <p>
            {humanizeToken(routing.complexity)} claim
            {routing.fast_path ? ' · clean path' : ''}
            {routing.is_multi_peril ? ' · multi-peril' : ''}
            {routing.missing_fields.length > 0
              ? ` · missing ${routing.missing_fields.map(humanizeToken).join(', ')}`
              : ''}
          </p>
        )}
      </WorkspaceSection>

      <WorkspaceSection
        title="Policy"
        state={sectionState(Boolean(retrieval), streaming && Boolean(routing) && !retrieval)}
        hint="Searching governing clauses."
      >
        {retrieval && (
          <p>
            {coverageHit === false
              ? retrieval.escalation_reason ?? 'No governing policy found.'
              : `${clauses.length} clause${clauses.length === 1 ? '' : 's'} retrieved.`}
          </p>
        )}
      </WorkspaceSection>

      <WorkspaceSection
        title="Eligibility"
        state={sectionState(Boolean(eligibility), streaming && coverageHit === true && !eligibility)}
        hint="Applying coverage rules."
      >
        {eligibility && (
          <p>
            Claimed {formatInr(eligibility.total_claimed)} → payable {formatInr(eligibility.total_payable)}
            {eligibility.needs_escalation ? ' · needs review' : ''}
          </p>
        )}
      </WorkspaceSection>

      <WorkspaceSection
        title="Risk"
        state={sectionState(Boolean(risk), streaming && coverageHit === true && !risk)}
        hint="Checking anomalies."
      >
        {risk && (
          <>
            <p className="muted" style={{ textTransform: 'capitalize' }}>
              Severity {risk.severity}
            </p>
            {risk.flags.length > 0 ? (
              <div className="kv" style={{ marginTop: 8 }}>
                {risk.flags.map((flag) => (
                  <span className="chip" key={flag}>
                    {formatRiskFlag(flag)}
                  </span>
                ))}
              </div>
            ) : (
              <p className="empty-note">No anomalies detected.</p>
            )}
          </>
        )}
      </WorkspaceSection>

      <WorkspaceSection
        title="Decision"
        state={sectionState(Boolean(decision), streaming && Boolean(eligibility) && Boolean(risk) && !decision)}
        hint="Composing the outcome."
      >
        {decision && (
          <>
            <p>
              <strong>{formatOutcome(decision.outcome)}</strong>
              {' · '}
              {formatInr(decision.amount)}
            </p>
            <p className="provisional-note">Computed — explanation next</p>
          </>
        )}
      </WorkspaceSection>

      <WorkspaceSection
        title="Explanation"
        state={sectionState(Boolean(explanation), streaming && Boolean(decision) && !explanation)}
        hint="Writing the rationale."
      >
        {explanation && <p>{explanation}</p>}
      </WorkspaceSection>
    </>
  )
}
