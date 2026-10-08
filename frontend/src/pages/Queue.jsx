import { useEffect, useMemo, useState } from 'react'
import { getQueue } from '../api'
import Async from '../components/Async'
import QueueTable from '../components/QueueTable'
import WeightControls from '../components/WeightControls'
import { hours } from '../lib/format'
import { useApi } from '../lib/useApi'
import { DEFAULT_WEIGHTS, normalizeWeights, weightsParam } from '../lib/weights'

const DEFAULT_CAPACITY = 40
const DEBOUNCE_MS = 300

export default function Queue() {
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
      return getQueue({ capacity: p.capacity, weights: p.weights, include_decided: p.includeDecided }, signal)
    },
    [applied],
  )

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-5">
        <h1 className="text-2xl font-semibold text-ink">Review queue</h1>
        <p className="mt-1 text-sm text-muted">
          Cases ranked by priority and scheduled against your team&apos;s hours. The ranking is advice; a
          person decides each case.
        </p>
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
            <QueueTable queue={data} />
          </>
        )}
      </Async>
    </div>
  )
}
