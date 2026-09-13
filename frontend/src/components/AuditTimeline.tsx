import {
  formatEventType,
  formatTimestamp,
  humanizeToken,
} from '../lib/format'
import type { AuditLogEntry } from '../types'

export function AuditTimeline({ entries }: { entries: AuditLogEntry[] }) {
  return (
    <details className="audit-log card">
      <summary>Audit log ({entries.length})</summary>
      {entries.length === 0 ? (
        <p className="empty-note">No events yet.</p>
      ) : (
        <ol className="audit-timeline">
          {entries.map((entry, i) => (
            <li className="audit-item" key={`${entry.timestamp}-${i}`}>
              <time dateTime={entry.timestamp}>{formatTimestamp(entry.timestamp)}</time>
              <div>
                <strong>
                  {formatEventType(entry.event_type)}
                  <span className="muted"> · {humanizeToken(entry.node)}</span>
                </strong>
                <p>{entry.detail}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </details>
  )
}
