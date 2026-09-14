import { useState, type FormEvent } from 'react'
import { policyOptionLabel } from '../lib/format'
import { detectDateInText, type ManualTestCase, type PolicyId } from '../manualTestCases'
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
  date_of_loss: '',
  claimant_name: '',
  claimant_gender: 'female',
  claimant_city: '',
  narrative_text: '',
}

interface MissingPromptState {
  missingLossDate: boolean
  missingAmount: boolean
  lossDateValue: string
  amountValue: string
}

interface Props {
  onSubmit: (submission: ClaimSubmission) => void
  disabled?: boolean
}

export function ClaimSubmitForm({ onSubmit, disabled }: Props) {
  const [fields, setFields] = useState<ClaimSubmission>(EMPTY)
  const [selectedTestId, setSelectedTestId] = useState<string | null>(null)
  const [missingPrompt, setMissingPrompt] = useState<MissingPromptState | null>(null)

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
      date_of_loss: '',
      claimant_name: '',
      claimant_gender: 'female',
      claimant_city: '',
      narrative_text: '',
    })
    setSelectedTestId(null)
    setMissingPrompt(null)
  }

  function finalizeAndSubmit(
    baseFields: ClaimSubmission,
    resolvedDate?: string | null,
    extraAmount?: string,
  ) {
    const submission = { ...baseFields }
    if (!submission.claim_id) delete submission.claim_id

    let narrative = submission.narrative_text.trim()

    if (resolvedDate) {
      submission.date_of_loss = resolvedDate
      // If narrative doesn't clearly mention a date, prepend it so the text is clear
      if (!detectDateInText(narrative)) {
        narrative = `Incident date: ${resolvedDate}. ${narrative}`
      }
    }

    if (
      extraAmount &&
      !/(?:₹|rs\.?|inr|\$|\bamount\b|\bclaiming\b|\bcost\b|\btotal\b)\s*[\d,]+|\b\d{3,}\b/i.test(narrative)
    ) {
      narrative = `${narrative} Claiming ₹${extraAmount} for loss/repairs.`
    }

    submission.narrative_text = narrative
    setFields((prev) => ({
      ...prev,
      date_of_loss: submission.date_of_loss || prev.date_of_loss,
      narrative_text: narrative,
    }))
    setMissingPrompt(null)
    onSubmit(submission)
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()

    const detectedDate =
      fields.date_of_loss?.trim() || detectDateInText(fields.narrative_text)
    const hasAmount =
      /(?:₹|rs\.?|inr|\$|\bamount\b|\bclaiming\b|\bcost\b|\btotal\b)\s*[\d,]+|\b\d{3,}\b/i.test(
        fields.narrative_text,
      )

    const missingLossDate = !detectedDate
    const missingAmount = !hasAmount

    // If required details have not been retrieved from the narrative or form, prompt the claimant
    if (missingLossDate || missingAmount) {
      setMissingPrompt({
        missingLossDate,
        missingAmount,
        lossDateValue: fields.date_of_loss || fields.filed_date || '',
        amountValue: '',
      })
      return
    }

    finalizeAndSubmit(fields, detectedDate)
  }

  function handleConfirmMissingDetails() {
    if (!missingPrompt) return
    if (missingPrompt.missingLossDate && !missingPrompt.lossDateValue) {
      return
    }
    finalizeAndSubmit(
      fields,
      missingPrompt.lossDateValue || fields.date_of_loss || null,
      missingPrompt.amountValue?.trim() || undefined,
    )
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
              <label htmlFor="date_of_loss">Incident date (Date of loss)</label>
              <input
                id="date_of_loss"
                type="date"
                value={fields.date_of_loss || ''}
                onChange={(e) => update('date_of_loss', e.target.value)}
                placeholder="YYYY-MM-DD"
              />
              <span className="field-help">
                When the loss or incident occurred. Evaluated for active coverage and waiting periods.
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

      {/* Verification / Details Retrieval Modal before adjudication */}
      {missingPrompt && (
        <div
          className="verification-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="verify-details-title"
        >
          <div className="verification-modal-card">
            <div className="verification-modal-header">
              <h3 id="verify-details-title">Confirm claim details before adjudication</h3>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setMissingPrompt(null)}
              >
                Close
              </button>
            </div>
            <div className="verification-modal-body">
              <div className="verification-notice">
                <strong>Required details confirmation</strong>
                <p>
                  Before proceeding to adjudicate the claim, all essential facts must be retrieved
                  from the claimant so that the issue is completely clear and the policy rules can evaluate
                  coverage, waiting periods, and deductible limits.
                </p>
              </div>

              {missingPrompt.missingLossDate && (
                <div className="field">
                  <label htmlFor="confirm_date_of_loss">
                    Incident date (Date of loss) *
                  </label>
                  <input
                    id="confirm_date_of_loss"
                    type="date"
                    value={missingPrompt.lossDateValue}
                    onChange={(e) =>
                      setMissingPrompt((prev) =>
                        prev ? { ...prev, lossDateValue: e.target.value } : null,
                      )
                    }
                    required
                  />
                  <span className="field-help">
                    Date when the incident or damage occurred. Required to evaluate waiting periods and policy eligibility.
                  </span>
                </div>
              )}

              {missingPrompt.missingAmount && (
                <div className="field">
                  <label htmlFor="confirm_amount">Claimed amount (₹)</label>
                  <input
                    id="confirm_amount"
                    type="number"
                    min="0"
                    placeholder="e.g. 15000"
                    value={missingPrompt.amountValue}
                    onChange={(e) =>
                      setMissingPrompt((prev) =>
                        prev ? { ...prev, amountValue: e.target.value } : null,
                      )
                    }
                  />
                  <span className="field-help">
                    Estimated or incurred expense being claimed from the policy.
                  </span>
                </div>
              )}
            </div>
            <div className="verification-modal-footer">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setMissingPrompt(null)}
              >
                Back to edit narrative
              </button>
              <button
                type="button"
                className="btn"
                disabled={missingPrompt.missingLossDate && !missingPrompt.lossDateValue}
                onClick={handleConfirmMissingDetails}
              >
                Confirm details & adjudicate
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
