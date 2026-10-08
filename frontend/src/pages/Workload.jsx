import { getWorkload } from '../api'
import Async from '../components/Async'
import { UnitReportButtons } from '../components/ReportButtons'
import { Badge, Card, Meter } from '../components/ui'
import { useAuth } from '../lib/authContext'
import { hours } from '../lib/format'
import { useApi } from '../lib/useApi'

/** A team lead's view of their unit: what each investigator holds, against their hours. */
export default function Workload() {
  const { user } = useAuth()
  const state = useApi((signal) => getWorkload(user.unit_id, signal), [user.unit_id])
  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-5">
        <h1 className="text-2xl font-semibold text-ink">Team Workload</h1>
        <p className="mt-1 text-sm text-muted">Open cases, high-priority cases and effort for each investigator in your unit.</p>
        <div className="mt-3">
          <UnitReportButtons unitId={user.unit_id} />
        </div>
      </div>
      <Async state={state} label="Loading the workload" rows={4}>
        {(w) => (
          <>
            <p className="mb-3 text-xs text-muted" data-testid="workload-summary">
              {w.unit_name} · {w.unassigned} unassigned · {w.overdue} overdue
            </p>
            <div className="grid gap-4 md:grid-cols-2" data-testid="workload-list">
              {w.people.length === 0 && <p className="text-sm text-muted">This unit has no active investigators.</p>}
              {w.people.map((p) => (
                <Card key={p.user_id} title={p.name} subtitle={`${p.assigned} open · ${p.in_review} in review · ${p.closed} closed`} data-testid="workload-card">
                  <div className="flex items-center gap-2 text-sm text-ink">
                    <span>Open high-priority cases</span>
                    <Badge tone={p.open_high_priority > 0 ? 'serious' : 'muted'}>{p.open_high_priority}</Badge>
                  </div>
                  <p className="mt-3 flex justify-between text-xs text-ink-2">
                    <span>Effort</span>
                    <span className="tabular-nums">
                      {hours(p.effort_hours)} of {hours(p.capacity_hours)}
                    </span>
                  </p>
                  <Meter value={p.effort_hours} max={p.capacity_hours} label={`${p.name}: effort against capacity`} className="mt-1" />
                  {p.effort_hours > p.capacity_hours && <p className="mt-1 text-xs text-warn">Over capacity</p>}
                  <p className="mt-2 text-xs text-muted">{p.decisions} decisions recorded</p>
                </Card>
              ))}
            </div>
          </>
        )}
      </Async>
    </div>
  )
}
