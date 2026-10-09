const TONES = {
  neutral: 'border-axis text-ink-2',
  accent: 'border-accent/60 text-accent bg-accent/10',
  good: 'border-good/50 text-good bg-good/10',
  warn: 'border-warn/50 text-warn bg-warn/10',
  serious: 'border-serious/60 text-serious bg-serious/10',
  critical: 'border-critical/60 text-flag bg-critical/10',
  muted: 'border-line text-muted',
}

export function Badge({ tone = 'neutral', icon, children, title, className = '', ...rest }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-medium ${TONES[tone]} ${className}`}
      {...rest}
    >
      {icon && <span aria-hidden="true">{icon}</span>}
      {children}
    </span>
  )
}

const BANDS = {
  Low: { tone: 'good', icon: '▼' },
  Medium: { tone: 'warn', icon: '●' },
  High: { tone: 'serious', icon: '▲' },
}

/** Risk band: colour plus an icon and the word, so colour never carries meaning alone. */
export function BandBadge({ band, className = '' }) {
  const { tone, icon } = BANDS[band] ?? { tone: 'muted', icon: '?' }
  return (
    <Badge tone={tone} icon={icon} className={className}>
      {band ?? 'Unknown'}
    </Badge>
  )
}

const CONFIDENCE = {
  High: { tone: 'good', icon: '✓' },
  Medium: { tone: 'warn', icon: '◐' },
  Low: { tone: 'serious', icon: '!' },
}

export function ConfidenceBadge({ level, reasons = [] }) {
  const { tone, icon } = CONFIDENCE[level] ?? { tone: 'muted', icon: '?' }
  return (
    <Badge tone={tone} icon={icon} title={reasons.join('; ')} data-testid="confidence-badge">
      Confidence: {level ?? 'Unknown'}
    </Badge>
  )
}

export function StatusBadge({ status }) {
  const waiting = status === 'Awaiting human review'
  return (
    <Badge tone={waiting ? 'warn' : 'accent'} icon={waiting ? '○' : '●'} data-testid="status-badge">
      {status}
    </Badge>
  )
}

export function Card({ title, subtitle, actions, children, className = '', id, ...rest }) {
  return (
    <section
      id={id}
      className={`themed rounded-xl border border-line bg-surface shadow-[var(--shadow-card)] ${className}`}
      aria-label={typeof title === 'string' ? title : undefined}
      {...rest}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function Button({ variant = 'default', className = '', ...props }) {
  const styles = {
    default: 'border-axis bg-surface-2 text-ink hover:border-accent hover:text-accent active:translate-y-px',
    primary: 'border-accent bg-accent text-white shadow-sm hover:bg-accent/90 active:translate-y-px',
    quiet: 'border-transparent text-ink-2 hover:bg-surface-2 hover:text-ink',
  }
  return (
    <button
      type="button"
      className={`rounded-md border px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${styles[variant]} ${className}`}
      {...props}
    />
  )
}

/** A small "i" that reveals text on hover or keyboard focus. */
export function InfoTip({ text, label = 'More information' }) {
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        aria-label={label}
        className="flex h-5 w-5 items-center justify-center rounded-full border border-axis text-xs text-ink-2 hover:border-accent"
      >
        i
      </button>
      <span
        role="tooltip"
        className="invisible absolute right-0 top-6 z-30 w-72 rounded-md border border-axis bg-surface-2 p-3 text-xs leading-relaxed text-ink-2 shadow-[var(--shadow-pop)] group-focus-within:visible group-hover:visible"
      >
        {text}
      </span>
    </span>
  )
}

export function Skeleton({ rows = 3, label = 'Loading' }) {
  return (
    <div role="status" aria-label={label} className="animate-pulse space-y-2">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-4 rounded bg-surface-2" style={{ width: `${90 - i * 12}%` }} />
      ))}
      <span className="sr-only">{label}…</span>
    </div>
  )
}

export function ErrorBox({ message, onRetry, title = 'Something went wrong' }) {
  return (
    <div role="alert" className="rounded-lg border border-critical/60 bg-critical/10 p-4">
      <p className="text-sm font-semibold text-flag">{title}</p>
      <p className="mt-1 text-sm text-ink-2">{message}</p>
      {onRetry && (
        <Button className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

/** A thin horizontal bar: the fill is a share of the track, with a lighter track behind it. */
export function Meter({ value, max = 1, label, className = '' }) {
  const share = Math.max(0, Math.min(1, max ? value / max : 0))
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      className={`h-2 overflow-hidden rounded-full bg-accent-dim/40 ${className}`}
    >
      <div className="h-full rounded-full bg-accent" style={{ width: `${share * 100}%` }} />
    </div>
  )
}
