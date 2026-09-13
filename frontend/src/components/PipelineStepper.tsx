import {
  ESCALATION_STAGE,
  PARALLEL,
  POST_PARALLEL,
  PRE_PARALLEL,
  deriveStageStates,
  pathCopy,
  type PipelineStatus,
  type PipelineStage,
  type StageState,
} from '../lib/pipeline'

function Step({ stage, state }: { stage: PipelineStage; state: StageState }) {
  return (
    <li className="pipeline-step" data-state={state}>
      <span className="pipeline-rail" aria-hidden />
      <span className="pipeline-label">{stage.label}</span>
    </li>
  )
}

interface Props {
  completed: string[]
  status: PipelineStatus
  fastPath?: boolean
}

export function PipelineStepper({ completed, status, fastPath }: Props) {
  const states = deriveStageStates(completed, status)

  return (
    <aside className="pipeline card">
      <h3>Pipeline</h3>
      <ol className="pipeline-list">
        {PRE_PARALLEL.map((stage) => (
          <Step key={stage.id} stage={stage} state={states[stage.id]} />
        ))}
        <li className="pipeline-parallel">
          <ol className="pipeline-parallel-list">
            {PARALLEL.map((stage) => (
              <Step key={stage.id} stage={stage} state={states[stage.id]} />
            ))}
          </ol>
        </li>
        {POST_PARALLEL.map((stage) => (
          <Step key={stage.id} stage={stage} state={states[stage.id]} />
        ))}
        <Step stage={ESCALATION_STAGE} state={states[ESCALATION_STAGE.id]} />
      </ol>
      <p className="pipeline-meta">{pathCopy(completed.length, status, fastPath)}</p>
    </aside>
  )
}
