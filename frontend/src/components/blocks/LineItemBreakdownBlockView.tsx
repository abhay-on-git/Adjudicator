import type { LineItemBreakdownBlock } from '../../types'

const VERDICT_LABEL: Record<LineItemBreakdownBlock['rows'][number]['verdict'], string> = {
  allowed: 'Allowed',
  reduced: 'Reduced',
  excluded: 'Excluded',
}

export function LineItemBreakdownBlockView({ block }: { block: LineItemBreakdownBlock }) {
  return (
    <div className="block line-item-breakdown-block">
      <h3>Line Items</h3>
      <table>
        <thead>
          <tr>
            <th>Description</th>
            <th>Claimed</th>
            <th>Allowed</th>
            <th>Verdict</th>
            <th>Clauses</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, i) => (
            <tr key={i} className={`verdict-${row.verdict}`}>
              <td>{row.description}</td>
              <td>₹{row.claimed_amount.toLocaleString('en-IN')}</td>
              <td>₹{row.allowed_amount.toLocaleString('en-IN')}</td>
              <td>{VERDICT_LABEL[row.verdict]}</td>
              <td>{row.governing_clause_ids.join(', ') || '—'}</td>
              <td>{row.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="deductible-note">Deductible applied: ₹{block.deductible_applied.toLocaleString('en-IN')}</div>
    </div>
  )
}
