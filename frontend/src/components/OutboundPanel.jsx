import { useState } from 'react'
import { errorMessage, getOutbound, postApprove, postOutbound, putOutbound } from '../api'
import { when } from '../lib/format'
import { useApi } from '../lib/useApi'
import { MIN_REASON, validateEntry } from '../lib/review'
import { useReviewer } from '../lib/reviewer'
import { Badge, Button } from './ui'

const field =
  'mt-1 w-full rounded-md border border-axis bg-page px-2 py-1.5 text-sm text-ink placeholder:text-muted'

const STATUS = {
  draft: { tone: 'warn', label: 'Draft' },
  approved: { tone: 'accent', label: 'Approved' },
  sent_simulated: { tone: 'good', label: 'Sent (simulated)' },
}

export const NOTICE =
  'This message will not be sent until you approve it. Do not mention suspicion or investigation.'

function Draft({ draft, onDone, onClose }) {
  const [reviewer, setReviewer] = useReviewer()
  const [subject, setSubject] = useState(draft.subject)
  const [body, setBody] = useState(draft.body)
  const [reason, setReason] = useState('')
  const [message, setMessage] = useState(null)
  const [busy, setBusy] = useState(false)

  async function run(work, okText) {
    setBusy(true)
    setMessage(null)
    try {
      await work()
      return true
    } catch (err) {
      setMessage({ type: 'error', text: errorMessage(err) }) // the validator's words are shown as they are
      return false
    } finally {
      setBusy(false)
      if (okText) setMessage((m) => m ?? { type: 'ok', text: okText })
    }
  }

  const save = () => run(() => putOutbound(draft.id, { subject, body }), 'Draft saved.')

  async function approve() {
    const problem = validateEntry(reviewer, reason)
    if (problem) {
      setMessage({ type: 'error', text: problem.replace('Enter your name before recording anything.', 'Enter the approver’s name.') })
      return
    }
    const saved = await run(() => putOutbound(draft.id, { subject, body }))
    if (!saved) return
    const ok = await run(() => postApprove(draft.id, { approved_by: reviewer.trim(), reason: reason.trim() }))
    if (ok) onDone()
  }

  return (
    <div className="mt-3 rounded-md border border-line p-3" data-testid="outbound-draft">
      <p role="note" className="rounded-md border border-warn/50 bg-warn/10 px-2 py-1.5 text-xs text-ink">
        {NOTICE}
      </p>
      <p className="mt-2 text-xs text-muted">
        To {draft.recipient_type}: {draft.recipient_id}
      </p>
      <label className="mt-2 block text-xs font-medium text-ink-2" htmlFor={`subject-${draft.id}`}>
        Subject
        <input id={`subject-${draft.id}`} className={field} value={subject} onChange={(e) => setSubject(e.target.value)} />
      </label>
      <label className="mt-2 block text-xs font-medium text-ink-2" htmlFor={`body-${draft.id}`}>
        Message
        <textarea id={`body-${draft.id}`} rows={7} className={field} value={body} onChange={(e) => setBody(e.target.value)} />
      </label>
      <label className="mt-2 block text-xs font-medium text-ink-2" htmlFor={`approver-${draft.id}`}>
        Approver
        <input id={`approver-${draft.id}`} className={field} value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="e.g. Asha Rao" />
      </label>
      <label className="mt-2 block text-xs font-medium text-ink-2" htmlFor={`approval-reason-${draft.id}`}>
        Reason for approving (required)
        <input
          id={`approval-reason-${draft.id}`}
          className={field}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder={`At least ${MIN_REASON} characters`}
        />
      </label>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="primary" disabled={busy} onClick={approve}>
          Approve &amp; send (simulated)
        </Button>
        <Button disabled={busy} onClick={save}>
          Save edits
        </Button>
        <Button variant="quiet" disabled={busy} onClick={onClose}>
          Close
        </Button>
      </div>
      {message && (
        <p
          role={message.type === 'error' ? 'alert' : 'status'}
          data-testid="outbound-message"
          className={`mt-2 text-xs ${message.type === 'error' ? 'text-flag' : 'text-good'}`}
        >
          {message.text}
        </p>
      )}
    </div>
  )
}

function Picker({ label, options, onPick, busy }) {
  const [choice, setChoice] = useState('')
  const value = choice || options[0] || ''
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        aria-label={`${label} recipient`}
        value={value}
        onChange={(e) => setChoice(e.target.value)}
        className="min-w-0 max-w-[10rem] rounded-md border border-axis bg-page px-1.5 py-1.5 text-xs text-ink"
        disabled={!options.length}
      >
        {options.map((o) => (
          <option key={o}>{o}</option>
        ))}
      </select>
      <Button disabled={busy || !value} onClick={() => onPick(value)}>
        {label}
      </Button>
    </div>
  )
}

/** Drafts of messages to providers and members. Nothing is sent until a person approves, and even
 *  then it is only simulated. */
export default function OutboundPanel({ detail }) {
  const [reviewer] = useReviewer()
  const history = useApi((signal) => getOutbound(detail.case_id, signal), [detail.case_id])
  const [openId, setOpenId] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const providers = detail.entity_ids.filter((id) => id.startsWith('PRV-'))
  const members = detail.affected_members.slice(0, 30)
  const items = history.data ?? []
  const open = items.find((m) => m.id === openId && m.status === 'draft')

  async function create(template, recipient_type, recipient_id) {
    setBusy(true)
    setError(null)
    try {
      const made = await postOutbound(detail.case_id, {
        template, recipient_type, recipient_id, created_by: reviewer.trim() || 'reviewer',
      })
      setOpenId(made.id)
      history.reload()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="outbound-title" data-testid="outbound-panel" className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 id="outbound-title" className="text-sm font-semibold text-ink">
          Messages to providers and members
        </h2>
        <Badge tone="muted">simulated</Badge>
      </div>
      <p className="mt-1 text-xs text-muted">Drafts only. Nothing leaves the platform.</p>
      <div className="mt-3 space-y-2">
        <Picker label="Request records" options={providers} busy={busy} onPick={(id) => create('records_request', 'provider', id)} />
        <Picker label="Verify with member" options={members} busy={busy} onPick={(id) => create('service_verification', 'member', id)} />
      </div>
      {error && (
        <p role="alert" className="mt-2 text-xs text-flag" data-testid="outbound-error">
          {error}
        </p>
      )}
      {open && <Draft key={open.id} draft={open} onClose={() => setOpenId(null)} onDone={() => { setOpenId(null); history.reload() }} />}
      <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-muted">History</h3>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-muted">No messages drafted for this case.</p>
      ) : (
        <ul className="mt-1 space-y-2" data-testid="outbound-history">
          {items.map((m) => {
            const s = STATUS[m.status] ?? STATUS.draft
            return (
              <li key={m.id} className="rounded-md border border-line p-2 text-xs text-ink-2">
                <p className="flex flex-wrap items-center gap-2">
                  <Badge tone={s.tone}>{s.label}</Badge>
                  <span className="font-medium text-ink">{m.subject}</span>
                </p>
                <p className="mt-0.5 text-muted">
                  To {m.recipient_type} {m.recipient_id} · {when(m.created_at)}
                  {m.approved_by ? ` · approved by ${m.approved_by}` : ''}
                </p>
                {m.status === 'draft' && (
                  <button type="button" className="mt-1 text-accent underline-offset-2 hover:underline" onClick={() => setOpenId(m.id)}>
                    Edit draft
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
