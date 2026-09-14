import { useMemo, useState } from 'react'
import { policyLabel } from '../lib/format'
import {
  testCasesForPolicy,
  type ManualTestCase,
  type ManualTestCategory,
} from '../manualTestCases'

const CATEGORY_CHIP_CLASS: Record<ManualTestCategory, string> = {
  'Happy path': 'verdict-allowed',
  'Boundary / sub-limit': 'verdict-reduced',
  'Exclusion / wrong claim': 'verdict-excluded',
  'Escalation / risk': 'category-chip-escalate',
  'Prompt injection': 'category-chip-injection',
}

interface Props {
  policyId: string
  selectedTestId: string | null
  onPrefill: (testCase: ManualTestCase) => void
  onRun: (testCase: ManualTestCase) => void
  onResetToCustom: () => void
}

export function TestCasePanel({
  policyId,
  selectedTestId,
  onPrefill,
  onRun,
  onResetToCustom,
}: Props) {
  const [isEnabled, setIsEnabled] = useState<boolean>(true)
  const [selectedCategory, setSelectedCategory] = useState<string>('All')
  const [searchQuery, setSearchQuery] = useState<string>('')

  const testCases = useMemo(() => testCasesForPolicy(policyId), [policyId])

  const selectedTestCase = useMemo(
    () => testCases.find((tc) => tc.id === selectedTestId) ?? null,
    [testCases, selectedTestId],
  )

  const categories = useMemo(() => {
    const cats = new Set<string>()
    testCases.forEach((tc) => cats.add(tc.category))
    return ['All', ...Array.from(cats)]
  }, [testCases])

  const filteredCases = useMemo(() => {
    return testCases.filter((tc) => {
      const matchesCat = selectedCategory === 'All' || tc.category === selectedCategory
      if (!matchesCat) return false
      if (!searchQuery.trim()) return true
      const q = searchQuery.toLowerCase()
      return (
        tc.id.toLowerCase().includes(q) ||
        tc.title.toLowerCase().includes(q) ||
        tc.intent.toLowerCase().includes(q) ||
        tc.expected.toLowerCase().includes(q) ||
        tc.fields.narrative_text.toLowerCase().includes(q)
      )
    })
  }, [testCases, selectedCategory, searchQuery])

  return (
    <section className="form-section test-cases-form-section" aria-labelledby="quick-tests-heading">
      <div className="test-cases-section-header">
        <div className="test-cases-title-group">
          <div className="title-with-pill">
            <h3 id="quick-tests-heading">Quick test cases</h3>
            <span className="status-chip">{testCases.length} scenarios</span>
          </div>
          <p className="section-copy">
            {testCases.length} scenarios for {policyLabel(policyId)}. Choose one to prefill the form,
            then edit any field or submit it as-is.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-secondary btn-sm toggle-test-cases-btn"
          onClick={() => setIsEnabled((prev) => !prev)}
          aria-expanded={isEnabled}
          aria-controls="quick-test-cases-content"
        >
          {isEnabled ? 'Hide test cases' : 'Enable test cases'}
        </button>
      </div>

      {isEnabled && (
        <div id="quick-test-cases-content" className="test-cases-section-content">
          {selectedTestCase && (
            <div className="active-scenario-banner">
              <div className="active-scenario-info">
                <span className="mono active-scenario-badge">{selectedTestCase.id}</span>
                <div className="active-scenario-meta">
                  <strong>{selectedTestCase.title}</strong>
                  <span>Scenario prefilled in form. You can edit any fields or submit as-is.</span>
                </div>
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={onResetToCustom}
                title="Clear prefilled values"
              >
                Clear
              </button>
            </div>
          )}

      {/* Filter and Search Bar */}
      <div className="test-cases-toolbar">
        <div className="category-filter-chips">
          {categories.map((cat) => (
            <button
              key={cat}
              type="button"
              className={`filter-pill ${selectedCategory === cat ? 'active' : ''}`}
              onClick={() => setSelectedCategory(cat)}
            >
              {cat === 'All' ? `All (${testCases.length})` : cat}
            </button>
          ))}
        </div>
        <div className="search-input-wrapper">
          <input
            type="search"
            className="search-input"
            placeholder="Search test cases…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </div>

      {/* FAQ-Style Accordion List */}
      <div className="faq-accordion">
        {filteredCases.length === 0 ? (
          <div className="test-cases-empty">
            <p>No scenarios found matching "{searchQuery || selectedCategory}".</p>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setSelectedCategory('All')
                setSearchQuery('')
              }}
            >
              Reset filters
            </button>
          </div>
        ) : (
          filteredCases.map((tc) => {
            const isSelected = selectedTestId === tc.id

            return (
              <details
                key={tc.id}
                className={`faq-item ${isSelected ? 'selected' : ''}`}
              >
                <summary className="faq-summary">
                  <div className="faq-summary-left">
                    <span className="mono faq-id">{tc.id}</span>
                    <strong className="faq-title">{tc.title}</strong>
                  </div>
                  <div className="faq-summary-right">
                    <span className={`verdict-chip ${CATEGORY_CHIP_CLASS[tc.category]}`}>
                      {tc.category}
                    </span>
                    <svg
                      className="faq-chevron"
                      width="16"
                      height="16"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </div>
                </summary>

                <div className="faq-body">
                  <div className="faq-detail-row">
                    <span className="faq-detail-label">Intent</span>
                    <p className="faq-detail-text">{tc.intent}</p>
                  </div>

                  <div className="faq-detail-row">
                    <span className="faq-detail-label">Expected behavior</span>
                    <p className="faq-detail-text">{tc.expected}</p>
                  </div>

                  <div className="faq-detail-row">
                    <span className="faq-detail-label">
                      Claimant story ({tc.fields.claimant_name}, {tc.fields.claimant_city})
                    </span>
                    <div className="faq-narrative-box">{tc.fields.narrative_text}</div>
                  </div>

                  <div className="faq-actions">
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => onPrefill(tc)}
                    >
                      {isSelected ? 'Prefilled' : 'Prefill form'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => onRun(tc)}
                    >
                      Run test
                    </button>
                  </div>
                </div>
              </details>
            )
          })
        )}
      </div>
    </div>
  )}
</section>

  )
}
