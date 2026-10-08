import { dateRange, rupees } from '../lib/format'
import { Badge } from './ui'

/** Dated events from the evidence, oldest first, each citing the finding behind it. */
export default function Timeline({ entries, onCite }) {
  if (!entries.length) {
    return <p className="text-sm text-muted">No dated events can be derived from the evidence.</p>
  }
  return (
    <ol className="relative space-y-4 border-l border-axis pl-5">
      {entries.map((e, i) => (
        <li key={`${e.date}-${e.kind}-${i}`} className="relative" data-testid="timeline-entry">
          <span aria-hidden="true" className="absolute -left-[1.6rem] top-1.5 h-2.5 w-2.5 rounded-full bg-accent ring-4 ring-surface" />
          <p className="text-xs font-medium tabular-nums text-ink">{dateRange(e.date, e.end_date)}</p>
          <div className="mt-0.5 flex flex-wrap items-center gap-2">
            <Badge tone="neutral">{e.kind}</Badge>
            <span className="text-sm text-ink-2">{e.description}</span>
            {e.amount > 0 && <span className="text-xs text-muted">({rupees(e.amount)})</span>}
            {e.evidence_keys.map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => onCite(k)}
                className="rounded border border-accent/50 bg-accent/10 px-1 text-xs font-medium text-accent hover:bg-accent/25"
              >
                [{k}]
              </button>
            ))}
          </div>
        </li>
      ))}
    </ol>
  )
}
