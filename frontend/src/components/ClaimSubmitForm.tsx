import { useState, type FormEvent } from 'react'
import { policyOptionLabel } from '../lib/format'
import type { ManualTestCase } from '../manualTestCases'
import type { ClaimSubmission } from '../types'
import { TestCasePanel } from './TestCasePanel'

const POLICIES = ['POL-HOME-01', 'POL-HEALTH-01', 'POL-MOTOR-01', 'POL-TRAVEL-01'] as const

const EMPTY: ClaimSubmission = {
  claim_id: '',
  policy_id: 'POL-HOME-01',
  policy_start_date: '2024-01-10',
  filed_date: '',
  claimant_name: '',
  claimant_gender: 'female',
  claimant_city: '',
  narrative_text: '',
}

interface Props {
  onSubmit: (submission: ClaimSubmission) => void
  disabled?: boolean
}

export function ClaimSubmitForm({ onSubmit, disabled }: Props) {
  const [fields, setFields] = useState<ClaimSubmission>(EMPTY)
  const [selectedTestId, setSelectedTestId] = useState<string | null>(null)

  function update<K extends keyof ClaimSubmission>(key: K, value: ClaimSubmission[K]) {
    setSelectedTestId(null)
    setFields((prev) => ({ ...prev, [key]: value }))
  }

  function applyTestCase(testCase: ManualTestCase) {
    setFields({ ...testCase.fields, claim_id: '' })
    setSelectedTestId(testCase.id)
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const submission = { ...fields }
    if (!submission.claim_id) delete submission.claim_id
    onSubmit(submission)
  }

  return (
    <div className="intake">
      <div className="intake-intro">
        <h2>Submit a claim</h2>
        <p>
          Enter the policy, claimant, and loss narrative. Facts are extracted from the story;
          coverage and payout are computed by rules — not by the model.
        </p>
      </div>
      <form className="claim-submit-form card" onSubmit={handleSubmit}>
        <section className="form-section">
          <h3>Policy</h3>
          <p className="section-copy">The schedule this claim will be read against.</p>
          <div className="field-grid">
            <div className="field">
              <label htmlFor="policy_id">Policy</label>
              <select
                id="policy_id"
                value={fields.policy_id}
                onChange={(e) => update('policy_id', e.target.value)}
              >
                {POLICIES.map((id) => (
                  <option key={id} value={id}>
                    {policyOptionLabel(id)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="policy_start_date">Policy start date</label>
              <input
                id="policy_start_date"
                type="date"
                value={fields.policy_start_date}
                onChange={(e) => update('policy_start_date', e.target.value)}
                required
              />
              <span className="field-help">
                Used for waiting-period rules; it is not checked against a policy schedule.
              </span>
            </div>
            <div className="field">
              <label htmlFor="filed_date">Filed date</label>
              <input
                id="filed_date"
                type="date"
                value={fields.filed_date}
                onChange={(e) => update('filed_date', e.target.value)}
                required
              />
            </div>
          </div>
        </section>

        <TestCasePanel
          policyId={fields.policy_id}
          selectedTestId={selectedTestId}
          onSelect={applyTestCase}
        />

        <section className="form-section">
          <h3>Claimant</h3>
          <p className="section-copy">Who is filing, and from where.</p>
          <div className="field-grid">
            <div className="field">
              <label htmlFor="claimant_name">Name</label>
              <input
                id="claimant_name"
                value={fields.claimant_name}
                onChange={(e) => update('claimant_name', e.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="claimant_city">City</label>
              <input
                id="claimant_city"
                value={fields.claimant_city}
                onChange={(e) => update('claimant_city', e.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="claimant_gender">Gender</label>
              <select
                id="claimant_gender"
                value={fields.claimant_gender}
                onChange={(e) => update('claimant_gender', e.target.value)}
                required
              >
                <option value="female">Female</option>
                <option value="male">Male</option>
                <option value="other">Other</option>
                <option value="undisclosed">Prefer not to say</option>
              </select>
            </div>
          </div>
        </section>

        <section className="form-section">
          <h3>Loss</h3>
          <p className="section-copy">Describe what happened, when, and what is being claimed.</p>
          <div className="field field-span">
            <label htmlFor="narrative_text">Narrative</label>
            <textarea
              id="narrative_text"
              rows={8}
              value={fields.narrative_text}
              onChange={(e) => update('narrative_text', e.target.value)}
              placeholder="On 30 July 2024 a pipe under my kitchen sink burst suddenly. Flooring near the sink was damaged. I am claiming ₹8,000 for repairs."
              required
            />
          </div>
        </section>

        <details className="advanced-toggle">
          <summary>Advanced: replay a fixture ID</summary>
          <div className="field">
            <label htmlFor="claim_id">Claim ID</label>
            <input
              id="claim_id"
              value={fields.claim_id ?? ''}
              onChange={(e) => update('claim_id', e.target.value)}
              placeholder="Leave blank to auto-generate, or use CLM-001"
            />
            <span className="field-help">Optional. Fixture IDs replay a known eval case.</span>
          </div>
        </details>

        <div className="form-actions">
          <button className="btn" type="submit" disabled={disabled}>
            {disabled ? 'Submitting…' : 'Adjudicate claim'}
          </button>
        </div>
      </form>
    </div>
  )
}
