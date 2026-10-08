import { Suspense, lazy } from 'react'
import { Link } from 'react-router-dom'
import { getHealth, getOverview } from '../api'
import Async from '../components/Async'
import { Badge, Card, Skeleton } from '../components/ui'
import { count, hours, rupees } from '../lib/format'
import { useApi } from '../lib/useApi'

const FindingsChart = lazy(() => import('../components/FindingsChart'))

function Tile({ label, value, sub, testId }) {
  return (
    <div className="min-w-0 flex-1 rounded-xl border border-line bg-surface p-4" data-testid={testId}>
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 text-3xl font-semibold text-ink">{value}</p>
      {sub && <p className="mt-1 text-xs text-muted">{sub}</p>}
    </div>
  )
}

function Arrow() {
  return (
    <span aria-hidden="true" className="hidden self-center text-xl text-axis md:block">
      →
    </span>
  )
}

function PipelineStatus() {
  const health = useApi((signal) => getHealth(signal), [])
  if (health.error && !health.data) {
    return <Badge tone="critical" icon="!">Pipeline status unavailable</Badge>
  }
  if (!health.data) return <Badge tone="muted">Checking pipeline…</Badge>
  const { status, total_seconds: seconds, stages } = health.data
  return (
    <Badge tone={status === 'ok' ? 'good' : 'warn'} icon={status === 'ok' ? '✓' : '!'}>
      Pipeline {status}
      {seconds != null && ` · ${seconds.toFixed(1)} s · ${stages.length} stages`}
    </Badge>
  )
}

export default function Overview() {
  const overview = useApi((signal) => getOverview(signal), [])
  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Overview</h1>
          <p className="mt-1 text-sm text-muted">
            Suspicious patterns that warrant review, from synthetic claims.
          </p>
        </div>
        <PipelineStatus />
      </div>

      <Async state={overview} label="Loading overview" rows={5}>
        {(o) => (
          <div className="space-y-6">
            <section aria-label="Amount at risk" className="rounded-xl border border-line bg-surface p-6">
              <p className="text-xs font-medium text-muted">Amount at risk</p>
              <p className="mt-1 text-5xl font-semibold tracking-tight text-ink" data-testid="amount-at-risk">
                {rupees(o.dollars_at_risk)}
              </p>
              <p className="mt-2 text-xs text-muted">
                Billed value of the distinct claims cited by claim-level findings. It is a flag for review,
                not a loss estimate.
              </p>
            </section>

            <section aria-label="From claims to scheduled cases" className="flex flex-col gap-3 md:flex-row">
              <Tile testId="tile-claims" label="Claims" value={count(o.claims)} sub="in the six-month window" />
              <Arrow />
              <Tile testId="tile-findings" label="Findings" value={count(o.findings)} sub="rules, anomaly model and graph" />
              <Arrow />
              <Tile
                testId="tile-cases"
                label="Cases"
                value={count(o.cases)}
                sub={`${o.cases_awaiting_review} awaiting review · ${o.cases_decided} decided`}
              />
              <Arrow />
              <Tile
                testId="tile-scheduled"
                label="Scheduled"
                value={count(o.cases_scheduled)}
                sub={`${hours(o.scheduled_hours)} of ${hours(o.team_hours)} team hours`}
              />
            </section>

            <Card title="Findings per rule" subtitle="How many findings each detector produced">
              <Suspense fallback={<Skeleton rows={4} label="Loading the chart" />}>
                <FindingsChart data={o.findings_per_rule} />
              </Suspense>
            </Card>

            <p className="text-sm text-ink-2">
              <Link to="/queue" className="text-accent underline-offset-2 hover:underline">
                Open the review queue →
              </Link>
            </p>
          </div>
        )}
      </Async>
    </div>
  )
}
