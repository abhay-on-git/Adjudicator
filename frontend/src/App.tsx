import { useState } from 'react'
import './App.css'
import { submitClaim } from './api'
import { ClaimStreamView } from './components/ClaimStreamView'
import { ClaimSubmitForm } from './components/ClaimSubmitForm'
import type { ClaimSubmission } from './types'

type ViewState =
  | { mode: 'form' }
  | { mode: 'streaming'; response: Response }
  | { mode: 'submit_error'; error: string }

export default function App() {
  const [view, setView] = useState<ViewState>({ mode: 'form' })
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(submission: ClaimSubmission) {
    setSubmitting(true)
    try {
      const response = await submitClaim(submission)
      setView({ mode: 'streaming', response })
    } catch (err) {
      setView({ mode: 'submit_error', error: err instanceof Error ? err.message : String(err) })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="app">
      <header>
        <h1>VenXR Adjudicator</h1>
      </header>
      <main>
        {view.mode === 'form' && <ClaimSubmitForm onSubmit={handleSubmit} disabled={submitting} />}
        {view.mode === 'submit_error' && (
          <div className="block error-panel">
            <h3>Could not submit claim</h3>
            <p>{view.error}</p>
            <button onClick={() => setView({ mode: 'form' })}>Back</button>
          </div>
        )}
        {view.mode === 'streaming' && (
          <ClaimStreamView initialResponse={view.response} onReset={() => setView({ mode: 'form' })} />
        )}
      </main>
    </div>
  )
}
