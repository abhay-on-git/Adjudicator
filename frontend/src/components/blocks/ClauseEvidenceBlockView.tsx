import type { ClauseEvidenceBlock } from '../../types'

export function ClauseEvidenceBlockView({ block }: { block: ClauseEvidenceBlock }) {
  if (block.clauses.length === 0) {
    return (
      <div className="block clause-evidence-block">
        <h3>Evidence</h3>
        <p className="empty-note">No governing clauses were cited.</p>
      </div>
    )
  }
  return (
    <div className="block clause-evidence-block">
      <h3>Evidence</h3>
      {block.clauses.map((clause) => (
        <div className="clause" key={clause.clause_id}>
          <div className="clause-heading">
            <span className="clause-id">{clause.clause_id}</span> {clause.title}
          </div>
          <p className="clause-text">{clause.text}</p>
        </div>
      ))}
    </div>
  )
}
