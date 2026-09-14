import { policyLabel } from '../lib/format'
import {
  testCasesForPolicy,
  type ManualTestCase,
  type ManualTestCategory,
} from '../manualTestCases'

const CATEGORY_CLASS: Record<ManualTestCategory, string> = {
  'Happy path': 'test-category-happy',
  'Boundary / sub-limit': 'test-category-boundary',
  'Exclusion / wrong claim': 'test-category-exclusion',
  'Escalation / risk': 'test-category-risk',
  'Prompt injection': 'test-category-injection',
}

interface Props {
  policyId: string
  selectedTestId: string | null
  onSelect: (testCase: ManualTestCase) => void
}

export function TestCasePanel({ policyId, selectedTestId, onSelect }: Props) {
  const testCases = testCasesForPolicy(policyId)

  return (
    <section className="test-case-panel" aria-labelledby="test-case-panel-title">
      <div className="test-case-panel-header">
        <div>
          <h3 id="test-case-panel-title">Quick test cases</h3>
          <p>
            {testCases.length} scenarios for {policyLabel(policyId)}. Choose one to
            prefill the form, then edit any field or submit it as-is.
          </p>
        </div>
        <span className="test-case-count">{testCases.length}</span>
      </div>

      <div className="test-case-list">
        {testCases.map((testCase) => (
          <details
            className={`test-case-item${selectedTestId === testCase.id ? ' selected' : ''}`}
            key={testCase.id}
          >
            <summary>
              <span className="test-case-summary-copy">
                <span className="test-case-id">{testCase.id}</span>
                <strong>{testCase.title}</strong>
              </span>
              <span className={`test-category ${CATEGORY_CLASS[testCase.category]}`}>
                {testCase.category}
              </span>
            </summary>
            <div className="test-case-body">
              <div>
                <span className="test-case-label">Intent</span>
                <p>{testCase.intent}</p>
              </div>
              <div>
                <span className="test-case-label">Expected behavior</span>
                <p>{testCase.expected}</p>
              </div>
              <button
                className="btn btn-secondary test-case-use"
                type="button"
                onClick={() => onSelect(testCase)}
              >
                {selectedTestId === testCase.id ? 'Prefilled — use again' : 'Prefill this case'}
              </button>
            </div>
          </details>
        ))}
      </div>
    </section>
  )
}
