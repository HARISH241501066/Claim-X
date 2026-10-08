import { useId } from 'react'
import { DEFAULT_WEIGHTS, WEIGHT_KEYS, WEIGHT_LABELS, percentShares } from '../lib/weights'
import { Button } from './ui'

/** Collapsible priority weights. Sliders are relative; the percentages shown always sum to 100. */
export default function WeightControls({ raw, onChange, open, onToggle }) {
  const panelId = useId()
  const shares = percentShares(raw)
  return (
    <div className="rounded-xl border border-line bg-surface">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-semibold text-ink"
      >
        <span>Priority weights</span>
        <span aria-hidden="true" className="text-muted">
          {open ? '▾' : '▸'}
        </span>
      </button>
      <div id={panelId} hidden={!open} className="border-t border-line px-4 py-3">
        <div className="grid gap-x-8 gap-y-3 md:grid-cols-2">
          {WEIGHT_KEYS.map((key) => (
            <label key={key} className="block text-xs text-ink-2">
              <span className="flex justify-between">
                <span>{WEIGHT_LABELS[key]}</span>
                <span className="tabular-nums text-ink" data-testid={`weight-${key}`}>
                  {shares ? `${shares[key]}%` : '–'}
                </span>
              </span>
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                value={raw[key]}
                onChange={(e) => onChange({ ...raw, [key]: Number(e.target.value) })}
                aria-label={`${WEIGHT_LABELS[key]} weight`}
                className="mt-1 w-full"
              />
            </label>
          ))}
        </div>
        <div className="mt-3 flex items-center justify-between">
          {shares ? (
            <p className="text-xs text-muted">Shares are relative and always add up to 100%.</p>
          ) : (
            <p role="alert" className="text-xs text-flag">
              Give at least one weight a value above zero.
            </p>
          )}
          <Button variant="quiet" onClick={() => onChange({ ...DEFAULT_WEIGHTS })}>
            Reset to defaults
          </Button>
        </div>
      </div>
    </div>
  )
}
