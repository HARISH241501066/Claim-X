import { useState } from 'react'
import { errorMessage, postDecision, postOverride } from '../api'
import { priority as fmtPriority, when } from '../lib/format'
import { ACTIONS, ACTION_LABELS, validateReason } from '../lib/review'
import DetectorChips from './DetectorChips'
import { Badge, Button, ConfidenceBadge } from './ui'

const field =
  'mt-1 w-full rounded-md border border-axis bg-page px-2 py-1.5 text-sm text-ink placeholder:text-muted'

function HistoryList({ entries }) {
  if (!entries.length) return <p className="text-xs text-muted">Nothing has been recorded for this case yet.</p>
  return (
    <ul className="space-y-2" data-testid="decision-history">
      {entries.map((e) => (
        <li key={e.audit_id} className="rounded-md border border-line p-2 text-xs text-ink-2">
          <p>
            <span className="font-medium text-ink">{ACTION_LABELS[e.action] ?? e.action}</span> · {e.reviewer} ·{' '}
            {when(e.ts)}
          </p>
          <p className="mt-0.5 text-muted">“{e.reason}”</p>
        </li>
      ))}
    </ul>
  )
}

export function AiRecommendation({ detail }) {
  const action = detail.recommended_action
  return (
    <section
      aria-labelledby="ai-recommendation"
      data-testid="ai-panel"
      className="rounded-xl border border-accent/40 bg-accent/5 p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="ai-recommendation" className="text-sm font-semibold text-ink">
          AI Recommendation
        </h2>
        <Badge tone="accent">advisory only</Badge>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-ink-2">
        {action?.text ?? 'Insufficient data for a recommendation.'}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <ConfidenceBadge level={detail.confidence?.level} reasons={detail.confidence?.reasons} />
        <DetectorChips fired={detail.detectors_fired} />
      </div>
      <p className="mt-3 text-xs text-muted">
        The system only recommends. It never denies a claim or blocks a payment.
      </p>
    </section>
  )
}

export default function ReviewPanel({ detail, onChanged }) {
  const [reason, setReason] = useState('')
  const [overrideReason, setOverrideReason] = useState('')
  const [draftLevel, setLevel] = useState(null) // null: follow the case's current priority
  const level = draftLevel ?? Math.round(detail.priority * 100)
  const [busy, setBusy] = useState(false)
  const [decisionMessage, setDecisionMessage] = useState(null)
  const [overrideMessage, setOverrideMessage] = useState(null)

  const history = [...detail.decisions, ...detail.overrides].sort((a, b) => b.audit_id - a.audit_id)

  async function submit(kind, send, text, clear) {
    const setMessage = kind === 'decision' ? setDecisionMessage : setOverrideMessage
    const problem = validateReason(text)
    if (problem) {
      setMessage({ type: 'error', text: problem })
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const result = await send()
      setMessage({
        type: 'ok',
        text:
          kind === 'decision'
            ? `Recorded: ${result.case_status}, by ${result.reviewer}.`
            : result.override_active
              ? `Priority set to ${fmtPriority(result.priority)} by ${result.reviewer}.`
              : 'Override cleared. The AI priority applies again.',
      })
      clear('')
      onChanged()
    } catch (err) {
      setMessage({ type: 'error', text: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }

  const decide = (action) =>
    submit(
      'decision',
      () => postDecision(detail.case_id, { action, reason: reason.trim() }),
      reason,
      setReason,
    )
  const override = (priority) =>
    submit(
      'override',
      () => postOverride(detail.case_id, { priority, reason: overrideReason.trim() }),
      overrideReason,
      (text) => {
        setOverrideReason(text)
        setLevel(null)
      },
    )

  return (
    <div className="space-y-4">
      <AiRecommendation detail={detail} />

      <section
        aria-labelledby="your-decision"
        data-testid="decision-panel"
        className="rounded-xl border-2 border-ink-2/30 bg-surface-2 p-4"
      >
        <div className="flex items-center justify-between gap-2">
          <h2 id="your-decision" className="text-sm font-semibold text-ink">
            Your Decision
          </h2>
          <Badge tone="muted">logged</Badge>
        </div>
        <p className="mt-1 text-xs text-muted">Recorded under your sign-in and your reason in the audit log.</p>

        <label htmlFor="decision-reason" className="mt-3 block text-xs font-medium text-ink-2">
          Reason (required)
          <textarea
            id="decision-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="Why are you making this decision?"
            aria-describedby="decision-feedback"
            className={field}
          />
        </label>

        <div className="mt-3 flex flex-wrap gap-2">
          {ACTIONS.map(({ action, label, variant }) => (
            <Button key={action} variant={variant} disabled={busy} onClick={() => decide(action)}>
              {label}
            </Button>
          ))}
        </div>
        <div id="decision-feedback" aria-live="polite" className="mt-2 min-h-5 text-sm">
          {decisionMessage?.type === 'error' && (
            <p role="alert" className="text-flag" data-testid="decision-error">
              {decisionMessage.text}
            </p>
          )}
          {decisionMessage?.type === 'ok' && (
            <p className="text-good" data-testid="decision-confirmation">
              {decisionMessage.text}
            </p>
          )}
        </div>

        <fieldset className="mt-4 border-t border-line pt-3">
          <legend className="pr-2 text-xs font-semibold text-ink">Override queue priority</legend>
          <p className="text-xs text-muted">
            AI priority {fmtPriority(detail.ai_priority)}
            {detail.override && (
              <>
                {' · '}
                <span className="text-warn">
                  yours: {fmtPriority(detail.override.priority)} ({detail.override.reviewer})
                </span>
              </>
            )}
          </p>
          <label htmlFor="override-level" className="mt-2 block text-xs text-ink-2">
            <span className="flex justify-between">
              <span>Your priority</span>
              <span className="tabular-nums text-ink">{(level / 100).toFixed(2)}</span>
            </span>
            <input
              id="override-level"
              type="range"
              min={0}
              max={100}
              step={1}
              value={level}
              onChange={(e) => setLevel(Number(e.target.value))}
              className="mt-1 w-full"
            />
          </label>
          <label htmlFor="override-reason" className="mt-2 block text-xs font-medium text-ink-2">
            Reason for the override (required)
            <textarea
              id="override-reason"
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              rows={2}
              placeholder="Why should this case move?"
              className={field}
            />
          </label>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button disabled={busy} onClick={() => override(level / 100)}>
              Set priority
            </Button>
            {detail.override && (
              <Button variant="quiet" disabled={busy} onClick={() => override(null)}>
                Clear override
              </Button>
            )}
          </div>
          <div aria-live="polite" className="mt-2 min-h-5 text-sm">
            {overrideMessage?.type === 'error' && (
              <p role="alert" className="text-flag" data-testid="override-error">
                {overrideMessage.text}
              </p>
            )}
            {overrideMessage?.type === 'ok' && (
              <p className="text-good" data-testid="override-confirmation">
                {overrideMessage.text}
              </p>
            )}
          </div>
        </fieldset>

        <div className="mt-3 border-t border-line pt-3">
          <h3 className="mb-2 text-xs font-semibold text-ink">History</h3>
          <HistoryList entries={history} />
        </div>
      </section>
    </div>
  )
}
