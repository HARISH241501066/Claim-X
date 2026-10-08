import { useEffect, useMemo, useState } from 'react'
import { getMembers, getQueue } from '../api'
import Async from '../components/Async'
import QueueTable from '../components/QueueTable'
import { UnitReportButtons } from '../components/ReportButtons'
import WeightControls from '../components/WeightControls'
import { useAuth } from '../lib/authContext'
import { hours } from '../lib/format'
import { useApi } from '../lib/useApi'
import { DEFAULT_WEIGHTS, normalizeWeights, weightsParam } from '../lib/weights'

const DEFAULT_CAPACITY = 40
const DEBOUNCE_MS = 300

const TEXT = {
  all: ['All cases', 'Every case in every unit, ranked by priority. The ranking is advice; a person decides each case.'],
  mine: ['My Cases', 'The cases assigned to you, ranked by priority and scheduled against your hours. The ranking is advice; you decide each case.'],
  unitReadOnly: ['Unit Queue', 'Your unit’s cases, read-only. You can open only the cases assigned to you.'],
  unit: ['Unit Queue', 'Your unit’s cases. Assign each one to an investigator; the ranking is advice and a person decides each case.'],
}

/** The part of a queue that satisfies `keep`, in the same order, as a smaller queue. */
function part(queue, keep) {
  const scheduled = queue.scheduled.filter(keep)
  return {
    ...queue,
    scheduled,
    backlog: queue.backlog.filter(keep),
    scheduled_hours: scheduled.reduce((sum, i) => sum + i.effort_hours, 0),
  }
}

export default function Queue({ mode = 'all' }) {
  const { user } = useAuth()
  const isLead = user.role === 'team_lead'
  const text = TEXT[mode === 'unit' && !isLead ? 'unitReadOnly' : mode]
  const [capacity, setCapacity] = useState(DEFAULT_CAPACITY)
  const [raw, setRaw] = useState({ ...DEFAULT_WEIGHTS })
  const [weightsOpen, setWeightsOpen] = useState(false)
  const [includeDecided, setIncludeDecided] = useState(false)

  // The request parameters that match the controls right now (null while the weights are invalid).
  const desired = useMemo(() => {
    const weights = normalizeWeights(raw)
    return weights ? JSON.stringify({ capacity, weights: weightsParam(weights), includeDecided }) : null
  }, [capacity, raw, includeDecided])

  // Wait for the sliders to settle before asking the API again.
  const [applied, setApplied] = useState(desired)
  useEffect(() => {
    if (desired === null) return undefined
    const id = setTimeout(() => setApplied(desired), DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [desired])

  const queue = useApi(
    (signal) => {
      const p = JSON.parse(applied)
      const view = user.role === 'investigator' ? (mode === 'unit' ? 'unit' : 'mine') : undefined
      return getQueue({ capacity: p.capacity, weights: p.weights, include_decided: p.includeDecided, view }, signal)
    },
    [applied, mode],
  )
  // a team lead assigns to investigators of their own unit
  const members = useApi(
    (signal) => (isLead ? getMembers(user.unit_id, signal) : Promise.resolve([])),
    [isLead, user.unit_id],
  )

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-5">
        <h1 className="text-2xl font-semibold text-ink">{text[0]}</h1>
        <p className="mt-1 text-sm text-muted">{text[1]}</p>
        {isLead && mode === 'unit' && (
          <div className="mt-3">
            <UnitReportButtons unitId={user.unit_id} />
          </div>
        )}
      </div>

      <div className="mb-4 grid gap-4 md:grid-cols-[1fr_auto]">
        <div className="rounded-xl border border-line bg-surface px-4 py-3">
          <label className="block text-sm text-ink-2" htmlFor="capacity">
            <span className="flex items-baseline justify-between">
              <span className="font-semibold text-ink">Team capacity</span>
              <span className="tabular-nums text-ink" data-testid="capacity-value">
                {hours(capacity)}
              </span>
            </span>
          </label>
          <input
            id="capacity"
            aria-label="Team capacity in hours"
            type="range"
            min={0}
            max={120}
            step={1}
            value={capacity}
            onChange={(e) => setCapacity(Number(e.target.value))}
            className="mt-2 w-full"
          />
          <p className="mt-1 text-xs text-muted">
            Provider cases take 4 h; a ring takes 4 h plus 1 h per linked entity.
          </p>
        </div>
        <label className="flex items-center gap-2 self-start rounded-xl border border-line bg-surface px-4 py-3 text-sm text-ink-2">
          <input type="checkbox" checked={includeDecided} onChange={(e) => setIncludeDecided(e.target.checked)} />
          Show decided cases
        </label>
      </div>

      <div className="mb-4">
        <WeightControls raw={raw} onChange={setRaw} open={weightsOpen} onToggle={() => setWeightsOpen((o) => !o)} />
      </div>

      <Async state={queue} label="Loading the queue" rows={6}>
        {(data) => (
          <>
            <p className="mb-2 text-xs text-muted" data-testid="queue-summary">
              {data.scheduled.length} scheduled ({hours(data.scheduled_hours)} of {hours(data.capacity_hours)}) ·{' '}
              {data.backlog.length} in backlog
              {data.decided_excluded > 0 && ` · ${data.decided_excluded} decided hidden`}
            </p>
            {isLead && mode === 'unit' ? (
              <>
                {[
                  ['Unassigned', (i) => i.assignment_status === 'unassigned'],
                  ['Assigned', (i) => i.assignment_status !== 'unassigned'],
                ].map(([title, keep]) => {
                  const section = part(data, keep)
                  return (
                    <section key={title} className="mb-6" aria-label={title}>
                      <h2 className="mb-2 text-sm font-semibold text-ink" data-testid={`section-${title.toLowerCase()}`}>
                        {title} ({section.scheduled.length + section.backlog.length})
                      </h2>
                      <QueueTable queue={section} members={members.data ?? []} onChanged={queue.reload} />
                    </section>
                  )
                })}
              </>
            ) : (
              <QueueTable queue={data} />
            )}
          </>
        )}
      </Async>
    </div>
  )
}
