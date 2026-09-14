import { useState, type FormEvent } from 'react'
import { policyOptionLabel } from '../lib/format'
import type { ManualTestCase, PolicyId } from '../manualTestCases'
import type { ClaimSubmission } from '../types'
import { TestCasePanel } from './TestCasePanel'

const POLICIES: PolicyId[] = [
  'POL-HOME-01',
  'POL-HEALTH-01',
  'POL-MOTOR-01',
  'POL-TRAVEL-01',
]

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

  function handlePolicyChange(newPolicyId: string) {
    setSelectedTestId(null)
    setFields((prev) => ({ ...prev, policy_id: newPolicyId }))
  }

  function handlePrefillTestCase(testCase: ManualTestCase) {
    setFields({ ...testCase.fields, claim_id: '' })
    setSelectedTestId(testCase.id)
  }

  function handleRunTestCase(testCase: ManualTestCase) {
    const submission = { ...testCase.fields }
    if (!submission.claim_id) delete submission.claim_id
    setFields({ ...testCase.fields, claim_id: '' })
    setSelectedTestId(testCase.id)
    onSubmit(submission)
  }

  function handleResetToCustom() {
    setFields({
      claim_id: '',
      policy_id: fields.policy_id,
      policy_start_date: fields.policy_start_date || '2024-01-10',
      filed_date: '',
      claimant_name: '',
      claimant_gender: 'female',
      claimant_city: '',
      narrative_text: '',
    })
    setSelectedTestId(null)
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
        {/* Section 1: Policy */}
        <section className="form-section">
          <h3>Policy</h3>
          <p className="section-copy">The schedule this claim will be read against.</p>
          <div className="field-grid">
            <div className="field">
              <label htmlFor="policy_id">Policy</label>
              <select
                id="policy_id"
                value={fields.policy_id}
                onChange={(e) => handlePolicyChange(e.target.value)}
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
              <span className="field-help">
                Checked against loss date for temporal consistency.
              </span>
            </div>
          </div>
        </section>

        {/* Section 2: Quick Test Cases (Native Form Section) */}
        <TestCasePanel
          policyId={fields.policy_id}
          selectedTestId={selectedTestId}
          onPrefill={handlePrefillTestCase}
          onRun={handleRunTestCase}
          onResetToCustom={handleResetToCustom}
        />

        {/* Section 3: Claimant */}
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
                placeholder="e.g. Priya Nair"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="claimant_city">City</label>
              <input
                id="claimant_city"
                value={fields.claimant_city}
                onChange={(e) => update('claimant_city', e.target.value)}
                placeholder="e.g. Pune"
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

        {/* Section 4: Narrative */}
        <section className="form-section">
          <h3>Narrative</h3>
          <p className="section-copy">Describe what happened, when, and what is being claimed.</p>
          <div className="field field-span">
            <textarea
              id="narrative_text"
              aria-label="Narrative"
              rows={8}
              value={fields.narrative_text}
              onChange={(e) => update('narrative_text', e.target.value)}
              placeholder="On 30 July 2024 a pipe under my kitchen sink burst suddenly. Flooring near the sink was damaged. I am claiming ₹8,000 for repairs."
              required
            />
          </div>
        </section>

        <div className="form-actions">
          <button
            type="button"
            className="btn btn-ghost"
            onClick={handleResetToCustom}
            disabled={disabled}
          >
            Reset to custom claim
          </button>
          <button className="btn" type="submit" disabled={disabled}>
            {disabled ? 'Submitting…' : 'Adjudicate claim'}
          </button>
        </div>
      </form>
    </div>
  )
}
