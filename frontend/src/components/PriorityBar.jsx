import { FACTORS } from '../lib/labels'

/** Priority as a bar plus number. If a reviewer overrode it, the AI value shows as a tick. */
export function PriorityBar({ priority, aiPriority, override }) {
  return (
    <div className="min-w-[8.5rem]">
      <div className="flex items-center gap-2">
        <div
          role="meter"
          aria-label="Priority"
          aria-valuemin={0}
          aria-valuemax={1}
          aria-valuenow={priority}
          className="relative h-2 flex-1 overflow-hidden rounded-full bg-accent-dim/40"
        >
          <div className="h-full rounded-full bg-accent" style={{ width: `${priority * 100}%` }} />
          {override && (
            <span
              aria-hidden="true"
              className="absolute top-0 h-full w-0.5 bg-ink"
              style={{ left: `${aiPriority * 100}%` }}
            />
          )}
        </div>
        <span className="w-9 text-right text-xs tabular-nums text-ink">{priority.toFixed(2)}</span>
      </div>
      {override && (
        <p className="mt-0.5 text-[11px] text-warn" title={override.reason}>
          AI {aiPriority.toFixed(2)} → {override.reviewer} {priority.toFixed(2)}
        </p>
      )}
    </div>
  )
}

/** Five tiny bars: risk, dollars, members, severity, evidence (each 0 to 1). */
export function FactorBars({ factors }) {
  const summary = FACTORS.map(([key, label]) => `${label} ${factors[key].toFixed(2)}`).join(', ')
  return (
    <div role="img" aria-label={`Factors: ${summary}`} title={summary} className="flex h-6 items-end gap-1">
      {FACTORS.map(([key, label]) => (
        <span key={key} className="flex h-full w-2 items-end overflow-hidden rounded-sm bg-accent-dim/40" title={`${label} ${factors[key].toFixed(2)}`}>
          <span className="block w-full rounded-sm bg-accent" style={{ height: `${Math.max(factors[key], 0.04) * 100}%` }} />
        </span>
      ))}
    </div>
  )
}
