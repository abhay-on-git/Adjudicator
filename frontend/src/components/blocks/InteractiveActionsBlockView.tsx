import type { InteractiveAction, InteractiveActionsBlock } from '../../types'

const ACTION_LABEL: Record<InteractiveAction, string> = {
  approve: 'Approve',
  override: 'Override',
  request_documents: 'Request Documents',
}

interface Props {
  block: InteractiveActionsBlock
  onResume?: (action: InteractiveAction) => void
  resuming?: boolean
}

export function InteractiveActionsBlockView({ block, onResume, resuming }: Props) {
  if (!block.is_pending) {
    return (
      <div className="block interactive-actions-block">
        <h3>Actions</h3>
        <p className="empty-note">Nothing pending — this decision is already final.</p>
      </div>
    )
  }
  return (
    <div className="block interactive-actions-block pending">
      <h3>Actions Needed</h3>
      <p>This claim is paused for human review (resumes at "{block.resumes_at_node}").</p>
      <div className="action-buttons">
        {block.available_actions.map((action) => (
          <button key={action} disabled={resuming} onClick={() => onResume?.(action)}>
            {ACTION_LABEL[action]}
          </button>
        ))}
      </div>
    </div>
  )
}
