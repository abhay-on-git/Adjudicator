import { formatInr } from '../../lib/format'
import type { LineItemBreakdownBlock } from '../../types'

const VERDICT_LABEL: Record<LineItemBreakdownBlock['rows'][number]['verdict'], string> = {
  allowed: 'Allowed',
  reduced: 'Reduced',
  excluded: 'Excluded',
}

export function LineItemBreakdownBlockView({ block }: { block: LineItemBreakdownBlock }) {
  return (
    <div className="block card line-item-breakdown-block">
      <h3>Line items</h3>
      <div className="line-item-table-wrap">
        <table className="line-item-table">
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
              <tr key={i}>
                <td>{row.description}</td>
                <td>{formatInr(row.claimed_amount)}</td>
                <td>{formatInr(row.allowed_amount)}</td>
                <td>
                  <span className={`verdict-chip verdict-${row.verdict}`}>
                    {VERDICT_LABEL[row.verdict]}
                  </span>
                </td>
                <td className="mono">{row.governing_clause_ids.join(', ') || '—'}</td>
                <td>{row.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="line-item-cards">
        {block.rows.map((row, i) => (
          <article className="line-item-card" key={i}>
            <strong>{row.description}</strong>
            <span className={`verdict-chip verdict-${row.verdict}`}>{VERDICT_LABEL[row.verdict]}</span>
            <p>
              Claimed {formatInr(row.claimed_amount)} → allowed {formatInr(row.allowed_amount)}
            </p>
            <p className="muted">{row.reason}</p>
            <p className="mono">{row.governing_clause_ids.join(', ') || '—'}</p>
          </article>
        ))}
      </div>
      <div className="deductible-note">Deductible applied: {formatInr(block.deductible_applied)}</div>
    </div>
  )
}
