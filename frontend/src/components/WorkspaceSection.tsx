import type { ReactNode } from 'react'

export type SectionState = 'pending' | 'live' | 'ready'

interface Props {
  title: string
  state: SectionState
  children?: ReactNode
  hint?: string
}

export function WorkspaceSection({ title, state, children, hint }: Props) {
  return (
    <section className="workspace-section card" data-state={state}>
      <div className="workspace-section-head">
        <h3>{title}</h3>
        {state === 'live' && <span className="live-pill">Live</span>}
        {state === 'pending' && <span className="pending-pill">Waiting</span>}
      </div>
      <div className="workspace-section-body">
        {state === 'pending' ? <p className="placeholder">{hint ?? 'Waiting for this step.'}</p> : children}
      </div>
    </section>
  )
}
