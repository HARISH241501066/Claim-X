import { useState } from 'react'
import { errorMessage, postAssign, postUnassign } from '../api'
import { ASSIGNMENT_LABELS } from '../lib/assignment'
import { MIN_REASON, validateReason } from '../lib/review'
import { Badge, Button } from './ui'

const field = 'mt-1 w-full rounded-md border border-axis bg-page px-2 py-1.5 text-xs text-ink placeholder:text-muted'

export function AssignmentBadge({ status }) {
  const s = ASSIGNMENT_LABELS[status] ?? ASSIGNMENT_LABELS.unassigned
  return (
    <Badge tone={s.tone} data-testid="assignment-status">
      {s.label}
    </Badge>
  )
}

/** A team lead's way to give a case to an investigator of their unit: pick, give a reason, confirm. */
export default function AssignControl({ caseId, members, assigneeId, onDone }) {
  const [open, setOpen] = useState(false)
  const others = members.filter((m) => m.id !== assigneeId)
  const [choice, setChoice] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const target = choice || (others[0] ? String(others[0].id) : '')

  async function run(work) {
    const problem = validateReason(reason)
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await work()
      setOpen(false)
      setReason('')
      setChoice('')
      onDone?.()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <Button onClick={() => setOpen(true)} className="!px-2 !py-1 text-xs" aria-label={`${assigneeId ? 'Reassign' : 'Assign'} ${caseId}`}>
        {assigneeId ? 'Reassign' : 'Assign to…'}
      </Button>
    )
  }
  return (
    <div className="mt-1 w-56 rounded-md border border-line bg-surface-2 p-2" data-testid="assign-form">
      <label className="block text-xs font-medium text-ink-2" htmlFor={`assignee-${caseId}`}>
        Assign to
        <select id={`assignee-${caseId}`} className={field} value={target} onChange={(e) => setChoice(e.target.value)}>
          {others.map((m) => (
            <option key={m.id} value={m.id}>
              {m.display_name}
            </option>
          ))}
        </select>
      </label>
      <label className="mt-2 block text-xs font-medium text-ink-2" htmlFor={`assign-reason-${caseId}`}>
        Reason (required)
        <input id={`assign-reason-${caseId}`} className={field} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={`At least ${MIN_REASON} characters`} />
      </label>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Button variant="primary" className="!px-2 !py-1 text-xs" disabled={busy || !target} onClick={() => run(() => postAssign(caseId, { assignee_user_id: Number(target), reason: reason.trim() }))}>
          Confirm
        </Button>
        {assigneeId && (
          <Button className="!px-2 !py-1 text-xs" disabled={busy} onClick={() => run(() => postUnassign(caseId, { reason: reason.trim() }))}>
            Unassign
          </Button>
        )}
        <Button variant="quiet" className="!px-2 !py-1 text-xs" disabled={busy} onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {error && (
        <p role="alert" data-testid="assign-error" className="mt-1.5 text-xs text-flag">
          {error}
        </p>
      )}
    </div>
  )
}
