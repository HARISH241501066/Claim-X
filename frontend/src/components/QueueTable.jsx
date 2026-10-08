import { Link } from 'react-router-dom'
import { count, hours, rupees } from '../lib/format'
import DetectorChips from './DetectorChips'
import { FactorBars, PriorityBar } from './PriorityBar'
import { Badge } from './ui'

function DividerRow({ children, testId, tone }) {
  return (
    <tr data-testid={testId}>
      <th
        colSpan={7}
        scope="colgroup"
        className={`border-y border-line px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide ${tone}`}
      >
        {children}
      </th>
    </tr>
  )
}

function CaseRow({ item }) {
  return (
    <tr className="border-b border-line align-middle hover:bg-surface-2/60" data-testid="queue-row" data-case={item.case_id}>
      <td className="px-3 py-3 text-center text-sm font-semibold tabular-nums text-ink">{item.rank}</td>
      <td className="px-3 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <Link to={`/cases/${item.case_id}`} className="text-sm font-medium text-ink underline-offset-2 hover:text-accent hover:underline">
            {item.title}
          </Link>
          <Badge tone={item.case_type === 'ring' ? 'critical' : 'neutral'}>
            {item.case_type === 'ring' ? 'Ring' : 'Provider'}
          </Badge>
        </div>
        <p className="mt-0.5 text-xs text-muted">
          {item.case_id}
          {item.investigation_band && ` · 30-day investigation risk ${item.investigation_band}`}
          {item.status !== 'Awaiting human review' && ` · ${item.status}`}
        </p>
      </td>
      <td className="px-3 py-3">
        <PriorityBar priority={item.priority} aiPriority={item.ai_priority} override={item.override} />
      </td>
      <td className="px-3 py-3">
        <FactorBars factors={item.factors} />
      </td>
      <td className="px-3 py-3">
        <DetectorChips fired={item.detectors_fired} />
      </td>
      <td className="px-3 py-3 text-right text-sm tabular-nums text-ink">{rupees(item.flagged_amount)}</td>
      <td className="px-3 py-3 text-right text-xs tabular-nums text-ink-2">{hours(item.effort_hours)}</td>
    </tr>
  )
}

/** The ranked table with a divider where the team's hours run out. */
export default function QueueTable({ queue }) {
  const { scheduled, backlog, capacity_hours: capacity, scheduled_hours: used } = queue
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface">
      <table className="w-full min-w-[56rem] border-collapse text-left">
        <caption className="sr-only">Cases ranked by priority, scheduled first and then backlog</caption>
        <thead>
          <tr className="text-xs text-muted">
            <th scope="col" className="px-3 py-2 text-center font-medium">Rank</th>
            <th scope="col" className="px-3 py-2 font-medium">Case</th>
            <th scope="col" className="px-3 py-2 font-medium">Priority</th>
            <th scope="col" className="px-3 py-2 font-medium" title="Risk, dollars, members, severity, evidence">Factors</th>
            <th scope="col" className="px-3 py-2 font-medium">Detectors</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Amount</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Effort</th>
          </tr>
        </thead>
        <tbody>
          <DividerRow testId="divider-scheduled" tone="bg-accent/10 text-ink">
            Scheduled · {count(scheduled.length)} {scheduled.length === 1 ? 'case' : 'cases'} · {hours(used)} of {hours(capacity)}
          </DividerRow>
          {scheduled.length === 0 && (
            <tr>
              <td colSpan={7} className="px-3 py-4 text-sm text-muted">
                No case fits in this many team hours.
              </td>
            </tr>
          )}
          {scheduled.map((item) => (
            <CaseRow key={item.case_id} item={item} />
          ))}
          <DividerRow testId="divider-backlog" tone="bg-surface-2 text-ink-2">
            Backlog · {count(backlog.length)} {backlog.length === 1 ? 'case' : 'cases'} waiting for capacity
          </DividerRow>
          {backlog.length === 0 && (
            <tr>
              <td colSpan={7} className="px-3 py-4 text-sm text-muted">
                Nothing is waiting: every case fits in the scheduled hours.
              </td>
            </tr>
          )}
          {backlog.map((item) => (
            <CaseRow key={item.case_id} item={item} />
          ))}
        </tbody>
      </table>
    </div>
  )
}
