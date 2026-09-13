import { useState } from 'react'
import { submitClaim } from './api'
import { ClaimStreamView } from './components/ClaimStreamView'
import { ClaimSubmitForm } from './components/ClaimSubmitForm'
import type { ClaimSubmission } from './types'

type ViewState =
  | { mode: 'form' }
  | { mode: 'streaming'; response: Response; submission: ClaimSubmission }
  | { mode: 'submit_error'; error: string }

export default function App() {
  const [view, setView] = useState<ViewState>({ mode: 'form' })
  const [submitting, setSubmitting] = useState(false)
  const [busy, setBusy] = useState(false)

  const onCase = view.mode !== 'form'

  async function handleSubmit(submission: ClaimSubmission) {
    setSubmitting(true)
    setBusy(true)
    try {
      const response = await submitClaim(submission)
      setView({ mode: 'streaming', response, submission })
    } catch (err) {
      setBusy(false)
      setView({ mode: 'submit_error', error: err instanceof Error ? err.message : String(err) })
    } finally {
      setSubmitting(false)
    }
  }

  function backToForm() {
    setBusy(false)
    setView({ mode: 'form' })
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            V
          </span>
          <div className="brand-text">
            <strong>VenXR</strong>
            <span>Adjudicator</span>
          </div>
        </div>
        <div className="header-actions">
          {busy && (
            <span className="status-chip" aria-live="polite">
              Adjudicating
            </span>
          )}
          {onCase && (
            <button type="button" className="btn btn-ghost" onClick={backToForm}>
              New claim
            </button>
          )}
        </div>
        {busy && <div className="app-header-progress" aria-hidden />}
      </header>
      <main className="app-main">
        {view.mode === 'form' && <ClaimSubmitForm onSubmit={handleSubmit} disabled={submitting} />}
        {view.mode === 'submit_error' && (
          <div className="card error-panel">
            <h3>Could not submit claim</h3>
            <p>{view.error}</p>
            <button type="button" className="btn btn-ghost" onClick={backToForm}>
              Back
            </button>
          </div>
        )}
        {view.mode === 'streaming' && (
          <ClaimStreamView
            initialResponse={view.response}
            submission={view.submission}
            onReset={backToForm}
            onBusyChange={setBusy}
          />
        )}
      </main>
    </div>
  )
}
