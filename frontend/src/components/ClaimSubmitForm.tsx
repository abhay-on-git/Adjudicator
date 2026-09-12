import { useState } from 'react'
import type { ClaimSubmission } from '../types'

const EMPTY: ClaimSubmission = {
  claim_id: '',
  policy_id: 'POL-HOME-01',
  policy_start_date: '2024-01-10',
  filed_date: '',
  claimant_name: '',
  claimant_gender: '',
  claimant_city: '',
  narrative_text: '',
}

interface Props {
  onSubmit: (submission: ClaimSubmission) => void
  disabled?: boolean
}

export function ClaimSubmitForm({ onSubmit, disabled }: Props) {
  const [fields, setFields] = useState<ClaimSubmission>(EMPTY)

  function update<K extends keyof ClaimSubmission>(key: K, value: ClaimSubmission[K]) {
    setFields((prev) => ({ ...prev, [key]: value }))
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const submission = { ...fields }
    if (!submission.claim_id) delete submission.claim_id
    onSubmit(submission)
  }

  return (
    <form className="claim-submit-form" onSubmit={handleSubmit}>
      <h2>Submit a Claim</h2>
      <label>
        Claim ID (optional — leave blank to auto-generate; use a fixture ID like CLM-001 to replay one)
        <input value={fields.claim_id ?? ''} onChange={(e) => update('claim_id', e.target.value)} />
      </label>
      <label>
        Policy ID
        <select value={fields.policy_id} onChange={(e) => update('policy_id', e.target.value)}>
          <option value="POL-HOME-01">POL-HOME-01</option>
          <option value="POL-HEALTH-01">POL-HEALTH-01</option>
          <option value="POL-MOTOR-01">POL-MOTOR-01</option>
          <option value="POL-TRAVEL-01">POL-TRAVEL-01</option>
        </select>
      </label>
      <label>
        Policy Start Date
        <input type="date" value={fields.policy_start_date} onChange={(e) => update('policy_start_date', e.target.value)} required />
      </label>
      <label>
        Filed Date
        <input type="date" value={fields.filed_date} onChange={(e) => update('filed_date', e.target.value)} required />
      </label>
      <label>
        Claimant Name
        <input value={fields.claimant_name} onChange={(e) => update('claimant_name', e.target.value)} required />
      </label>
      <label>
        Claimant Gender
        <input value={fields.claimant_gender} onChange={(e) => update('claimant_gender', e.target.value)} required />
      </label>
      <label>
        Claimant City
        <input value={fields.claimant_city} onChange={(e) => update('claimant_city', e.target.value)} required />
      </label>
      <label>
        Narrative
        <textarea
          rows={5}
          value={fields.narrative_text}
          onChange={(e) => update('narrative_text', e.target.value)}
          required
        />
      </label>
      <button type="submit" disabled={disabled}>
        Submit Claim
      </button>
    </form>
  )
}
