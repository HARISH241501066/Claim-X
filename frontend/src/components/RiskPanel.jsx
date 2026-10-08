import { riskPercent } from '../lib/format'
import { HORIZONS, isTrained, tooltipText } from '../lib/risk'
import { BandBadge, Badge, Card, InfoTip } from './ui'

/** The model's investigation risk for the case, with band, drivers and a plain-language caveat. */
export default function RiskPanel({ prediction, horizon = 30, onHorizon = () => {} }) {
  const available = prediction?.available
  const title = `${horizon}-Day Investigation Risk: ${available ? riskPercent(prediction.investigation_risk) : 'Insufficient data'}`
  const escalated = available && prediction.band_source === 'escalated'
  return (
    <Card
      title={title}
      actions={<InfoTip text={tooltipText(horizon)} label="About this estimate" />}
      data-testid="risk-panel"
    >
      <div role="radiogroup" aria-label="Prediction window" className="mb-3 inline-flex rounded-md border border-axis p-0.5">
        {HORIZONS.map((h) => (
          <button
            key={h}
            type="button"
            role="radio"
            aria-checked={h === horizon}
            disabled={!isTrained(h)}
            title={isTrained(h) ? `${h}-day window` : `The ${h}-day model is not trained yet`}
            onClick={() => onHorizon(h)}
            className={`rounded px-3 py-1 text-xs font-medium ${
              h === horizon ? 'bg-accent/20 text-ink' : 'text-ink-2'
            } disabled:cursor-not-allowed disabled:opacity-40`}
          >
            {h} days
          </button>
        ))}
      </div>

      {!available ? (
        <p className="text-sm text-muted">{prediction?.reason ?? 'Insufficient data'}</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <BandBadge band={prediction.risk_band} />
            {prediction.low_confidence && <Badge tone="warn" icon="!">Low confidence: under 60 days of history</Badge>}
            <span className="text-xs text-muted">
              for {prediction.provider_id} · {prediction.history_days} days of history
            </span>
          </div>
          {escalated && (
            <p className="rounded-md border border-warn/40 bg-warn/10 p-2 text-xs leading-relaxed text-ink-2" data-testid="band-escalated">
              {prediction.band_reason}. The model&apos;s own estimate is {riskPercent(prediction.investigation_risk)}.
            </p>
          )}
          <div>
            <p className="text-xs font-medium text-muted">Top drivers</p>
            {prediction.top_drivers.length ? (
              <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-sm text-ink-2">
                {prediction.top_drivers.slice(0, 3).map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ol>
            ) : (
              <p className="mt-1 text-sm text-muted">No single factor stands out.</p>
            )}
          </div>
        </div>
      )}
    </Card>
  )
}
