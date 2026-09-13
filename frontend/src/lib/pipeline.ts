export type PipelineStatus = 'streaming' | 'escalated' | 'done' | 'error'
export type StageState = 'waiting' | 'active' | 'done' | 'skipped'

export interface PipelineStage {
  id: string
  label: string
}

export const PRE_PARALLEL: PipelineStage[] = [
  { id: 'intake_normalize', label: 'Intake' },
  { id: 'extraction', label: 'Extract facts' },
  { id: 'router', label: 'Route' },
  { id: 'policy_retrieval', label: 'Find policy' },
]

export const PARALLEL: PipelineStage[] = [
  { id: 'eligibility_evaluation', label: 'Eligibility' },
  { id: 'risk_anomaly', label: 'Risk' },
]

export const POST_PARALLEL: PipelineStage[] = [
  { id: 'decision_composition', label: 'Decision' },
  { id: 'explanation', label: 'Explanation' },
  { id: 'ui_composition', label: 'Compose' },
]

export const ESCALATION_STAGE: PipelineStage = {
  id: 'escalation',
  label: 'Human review',
}

export const ALL_STAGE_IDS = [
  ...PRE_PARALLEL.map((s) => s.id),
  ...PARALLEL.map((s) => s.id),
  ...POST_PARALLEL.map((s) => s.id),
  ESCALATION_STAGE.id,
]

export function deriveStageStates(
  completed: string[],
  status: PipelineStatus,
): Record<string, StageState> {
  const done = new Set(completed)
  const states: Record<string, StageState> = {}
  for (const id of ALL_STAGE_IDS) {
    states[id] = done.has(id) ? 'done' : 'waiting'
  }

  const terminal = status === 'done' || status === 'error' || status === 'escalated'
  if (terminal) {
    for (const id of ALL_STAGE_IDS) {
      if (states[id] === 'waiting') states[id] = 'skipped'
    }
    if (status === 'escalated' && !done.has(ESCALATION_STAGE.id)) {
      states[ESCALATION_STAGE.id] = 'active'
    }
    return states
  }

  const firstIncomplete = (stages: PipelineStage[]) => stages.find((s) => !done.has(s.id))

  const nextPre = firstIncomplete(PRE_PARALLEL)
  if (nextPre) {
    states[nextPre.id] = 'active'
    return states
  }

  const parallelPending = PARALLEL.filter((s) => !done.has(s.id))
  if (parallelPending.length > 0) {
    for (const stage of parallelPending) states[stage.id] = 'active'
    return states
  }

  const nextPost = firstIncomplete(POST_PARALLEL)
  if (nextPost) {
    states[nextPost.id] = 'active'
    return states
  }

  if (!done.has(ESCALATION_STAGE.id)) {
    states[ESCALATION_STAGE.id] = 'waiting'
  }
  return states
}

export function pathCopy(completedCount: number, status: PipelineStatus, fastPath?: boolean): string {
  const steps = `${completedCount} step${completedCount === 1 ? '' : 's'}`
  if (status === 'escalated') return `${steps} · review path`
  if (status === 'error') return `${steps} · interrupted`
  if (fastPath) return `${steps} · clean path`
  if (status === 'done') return `${steps} · complete`
  return `${steps} · in progress`
}
