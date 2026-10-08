const CHIPS = [
  ['rules', 'Rules'],
  ['anomaly', 'ML'],
  ['graph', 'Graph'],
]

/** Rules · ML · Graph, with the detector groups that fired highlighted. */
export default function DetectorChips({ fired = [] }) {
  const names = CHIPS.filter(([key]) => fired.includes(key)).map(([, label]) => label)
  return (
    <span
      className="inline-flex items-center gap-1"
      role="img"
      aria-label={`Detectors fired: ${names.length ? names.join(', ') : 'none'}`}
    >
      {CHIPS.map(([key, label], i) => {
        const on = fired.includes(key)
        return (
          <span key={key} className="inline-flex items-center gap-1">
            {i > 0 && (
              <span aria-hidden="true" className="text-axis">
                ·
              </span>
            )}
            <span
              data-on={on}
              className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                on ? 'bg-accent/20 text-ink ring-1 ring-accent/60' : 'text-muted ring-1 ring-line'
              }`}
            >
              {label}
            </span>
          </span>
        )
      })}
    </span>
  )
}
