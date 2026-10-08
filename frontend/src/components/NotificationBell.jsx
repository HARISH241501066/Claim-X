import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { getNotifications, postNotificationRead, postReadAll } from '../api'
import { when } from '../lib/format'
import { useApi } from '../lib/useApi'
import { Badge } from './ui'

const POLL_MS = 30000
const GROUPS = [
  { severity: 'high', title: 'High priority' },
  { severity: 'warning', title: 'Warnings' },
  { severity: 'info', title: 'Information' },
]

function Envelope() {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <rect x="2.5" y="4.5" width="15" height="11" rx="1.5" />
      <path d="m3 6 7 5 7-5" />
    </svg>
  )
}

function BellIcon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M5 8a5 5 0 0 1 10 0c0 4 1.5 5 1.5 5h-13S5 12 5 8Z" />
      <path d="M8.5 16a1.7 1.7 0 0 0 3 0" />
    </svg>
  )
}

function Item({ note, onOpen }) {
  const high = note.severity === 'high'
  const body = (
    <>
      <span className="flex items-start justify-between gap-2">
        <span className={`text-sm ${note.read ? 'text-ink-2' : 'font-medium text-ink'}`}>{note.message}</span>
        {note.email_status === 'sent' && (
          <span className="mt-0.5 shrink-0 text-accent" title="An email was sent to the SIU team" data-testid="email-sent">
            <Envelope />
            <span className="sr-only">Email sent</span>
          </span>
        )}
      </span>
      <span className="mt-0.5 block text-xs text-muted">{when(note.created_at)}</span>
    </>
  )
  const style = `block rounded-md border px-3 py-2 ${
    high ? 'border-critical/60 bg-critical/10' : 'border-line bg-surface-2'
  } ${note.read ? 'opacity-70' : ''}`
  return (
    <li data-testid="notification" data-severity={note.severity} data-unread={!note.read}>
      {note.case_id ? (
        <Link to={`/cases/${note.case_id}`} className={`${style} hover:border-accent`} onClick={() => onOpen(note)}>
          {body}
        </Link>
      ) : (
        <button type="button" className={`${style} w-full text-left hover:border-accent`} onClick={() => onOpen(note)}>
          {body}
        </button>
      )}
    </li>
  )
}

/** The bell in the top bar: unread count, a list grouped by severity (high first), mark all read. */
export default function NotificationBell() {
  const [open, setOpen] = useState(false)
  const box = useRef(null)
  const state = useApi((signal) => getNotifications({}, signal), [])
  const { reload } = state

  useEffect(() => {
    const id = setInterval(reload, POLL_MS)
    return () => clearInterval(id)
  }, [reload])

  useEffect(() => {
    if (!open) return undefined
    const close = (e) => {
      if (e.type === 'keydown' ? e.key === 'Escape' : !box.current?.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])

  const notes = state.data?.notifications ?? []
  const unread = state.data?.unread_count ?? 0

  const openNote = async (note) => {
    setOpen(false)
    if (!note.read) {
      try {
        await postNotificationRead(note.id)
      } catch {
        /* the count refreshes on the next poll */
      }
      reload()
    }
  }
  const readAll = async () => {
    try {
      await postReadAll()
    } catch {
      /* the list stays as it was */
    }
    reload()
  }

  return (
    <div className="relative" ref={box}>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="true"
          aria-label={`Notifications, ${unread} unread`}
          data-testid="bell"
          className="relative rounded-md border border-axis bg-surface-2 p-2 text-ink hover:border-accent"
        >
          <BellIcon />
          {unread > 0 && (
            <span
              data-testid="unread-count"
              className="absolute -right-1.5 -top-1.5 min-w-[1.25rem] rounded-full bg-critical px-1 text-center text-[11px] font-semibold leading-5 text-white"
            >
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </button>
      </div>

      {open && (
        <div
          role="region"
          aria-label="Notifications"
          data-testid="notification-panel"
          className="absolute right-0 z-30 mt-2 w-[min(24rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-3 shadow-lg"
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">Notifications</h2>
            <button
              type="button"
              onClick={readAll}
              disabled={unread === 0}
              className="text-xs text-accent underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:text-muted disabled:no-underline"
            >
              Mark all read
            </button>
          </div>
          {state.error && !state.data && <p className="text-xs text-warn">{state.error}</p>}
          {state.data && notes.length === 0 && <p className="text-xs text-muted">Nothing new. You are up to date.</p>}
          <div className="max-h-[70vh] space-y-3 overflow-y-auto">
            {GROUPS.map(({ severity, title }) => {
              const group = notes.filter((n) => n.severity === severity)
              if (!group.length) return null
              return (
                <section key={severity} aria-label={title}>
                  <h3 className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted">
                    {title} <Badge tone={severity === 'high' ? 'critical' : 'muted'}>{group.length}</Badge>
                  </h3>
                  <ul className="space-y-1.5">
                    {group.map((n) => (
                      <Item key={n.id} note={n} onOpen={openNote} />
                    ))}
                  </ul>
                </section>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
