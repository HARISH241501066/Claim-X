import { useState } from 'react'
import {
  errorMessage, getUnits, getUnrouted, getUsers, postPrewarm, postRerun, postTestEmail, postUser, postUserActive,
} from '../api'
import Async from '../components/Async'
import { Badge, Button, Card } from '../components/ui'
import { useAuth } from '../lib/authContext'
import { ROLE_LABELS } from '../lib/roles'
import { useApi } from '../lib/useApi'

const field = 'mt-1 w-full rounded-md border border-axis bg-page px-2 py-1.5 text-sm text-ink placeholder:text-muted'

function Task({ title, subtitle, label, run, describe }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  async function go() {
    setBusy(true)
    setResult(null)
    try {
      setResult({ ok: true, text: describe(await run()) })
    } catch (err) {
      setResult({ ok: false, text: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card title={title} subtitle={subtitle}>
      <Button variant="primary" disabled={busy} onClick={go}>
        {busy ? 'Working…' : label}
      </Button>
      {result && (
        <p role={result.ok ? 'status' : 'alert'} data-testid="task-result" className={`mt-3 text-sm ${result.ok ? 'text-good' : 'text-flag'}`}>
          {result.text}
        </p>
      )}
    </Card>
  )
}

function NewUser({ units, onCreated }) {
  const [form, setForm] = useState({ username: '', display_name: '', role: 'investigator', unit_id: '', password: '' })
  const [message, setMessage] = useState(null)
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value })
  async function create(e) {
    e.preventDefault()
    setMessage(null)
    try {
      await postUser({ ...form, unit_id: form.role === 'admin' ? null : Number(form.unit_id) || null })
      setForm({ ...form, username: '', display_name: '', password: '' })
      setMessage({ ok: true, text: 'User created.' })
      onCreated()
    } catch (err) {
      setMessage({ ok: false, text: errorMessage(err) })
    }
  }
  return (
    <form onSubmit={create} className="mt-4 grid gap-2 sm:grid-cols-2" aria-label="Add a user">
      <label className="text-xs font-medium text-ink-2">
        Username
        <input className={field} value={form.username} onChange={set('username')} />
      </label>
      <label className="text-xs font-medium text-ink-2">
        Display name
        <input className={field} value={form.display_name} onChange={set('display_name')} />
      </label>
      <label className="text-xs font-medium text-ink-2">
        Role
        <select className={field} value={form.role} onChange={set('role')}>
          {Object.entries(ROLE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label className="text-xs font-medium text-ink-2">
        Unit
        <select className={field} value={form.unit_id} onChange={set('unit_id')} disabled={form.role === 'admin'}>
          <option value="">Choose…</option>
          {units.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}
            </option>
          ))}
        </select>
      </label>
      <label className="text-xs font-medium text-ink-2 sm:col-span-2">
        Initial password (8+ characters)
        <input type="password" className={field} value={form.password} onChange={set('password')} autoComplete="new-password" />
      </label>
      <div className="sm:col-span-2">
        <Button type="submit">Add user</Button>
        {message && (
          <span role={message.ok ? 'status' : 'alert'} className={`ml-3 text-xs ${message.ok ? 'text-good' : 'text-flag'}`}>
            {message.text}
          </span>
        )}
      </div>
    </form>
  )
}

/** System tasks and user and unit management. Only an administrator reaches this page. */
export default function Admin() {
  const { user: me } = useAuth()
  const users = useApi((signal) => getUsers(signal), [])
  const units = useApi((signal) => getUnits(signal), [])
  const unrouted = useApi((signal) => getUnrouted(signal), [])
  const [error, setError] = useState(null)

  async function toggle(u) {
    setError(null)
    try {
      await postUserActive(u.id, !u.active)
      users.reload()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <h1 className="text-2xl font-semibold text-ink">System</h1>
      <div className="grid gap-5 md:grid-cols-3">
        <Task
          title="Pipeline"
          subtitle="Rebuild the data and analysis. The current data keeps being served meanwhile."
          label="Rerun the pipeline"
          run={postRerun}
          describe={(h) => `Rerun finished: status ${h.status}.`}
        />
        <Task
          title="Briefs"
          subtitle="Write and store the briefs of the top 5 cases."
          label="Prewarm briefs"
          run={() => postPrewarm(5)}
          describe={(r) => `${r.generated} written, ${r.already_cached} already stored, ${r.fell_back} used the template.`}
        />
        <Task
          title="Email to the SIU team"
          subtitle="One test message through the same channel, with no case data."
          label="Send test email"
          run={postTestEmail}
          describe={(r) => `${r.ok ? 'Success. ' : 'Not sent. '}${r.detail}`}
        />
      </div>

      <Card title="Unrouted cases" subtitle="No unit covers the city of the provider. Only you can see these cases.">
        <Async state={unrouted} label="Loading" rows={2}>
          {(ids) =>
            ids.length ? (
              <p className="text-sm text-ink" data-testid="unrouted">
                {ids.join(', ')}
              </p>
            ) : (
              <p className="text-sm text-muted" data-testid="unrouted">
                None. Every case is routed to a unit.
              </p>
            )
          }
        </Async>
      </Card>

      <Card title="Units">
        <Async state={units} label="Loading units" rows={2}>
          {(list) => (
            <table className="w-full text-left text-sm" data-testid="units-table">
              <thead className="text-xs text-muted">
                <tr>
                  <th className="py-1 font-medium">Unit</th>
                  <th className="py-1 font-medium">Covers</th>
                </tr>
              </thead>
              <tbody>
                {list.map((u) => (
                  <tr key={u.id} className="border-t border-line">
                    <td className="py-1.5 text-ink">{u.name}</td>
                    <td className="py-1.5 text-ink-2">{u.region.join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      </Card>

      <Card title="Users" subtitle="Switching a user off ends their session at once.">
        {error && (
          <p role="alert" className="mb-2 text-xs text-flag">
            {error}
          </p>
        )}
        <Async state={users} label="Loading users" rows={3}>
          {(list) => (
            <>
              <table className="w-full text-left text-sm" data-testid="users-table">
                <thead className="text-xs text-muted">
                  <tr>
                    <th className="py-1 font-medium">User</th>
                    <th className="py-1 font-medium">Role</th>
                    <th className="py-1 font-medium">Unit</th>
                    <th className="py-1 font-medium">Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map((u) => (
                    <tr key={u.id} className="border-t border-line">
                      <td className="py-1.5 text-ink">
                        {u.display_name} <span className="text-xs text-muted">({u.username})</span>
                      </td>
                      <td className="py-1.5 text-ink-2">{ROLE_LABELS[u.role]}</td>
                      <td className="py-1.5 text-ink-2">{u.unit_name ?? '—'}</td>
                      <td className="py-1.5">
                        <Badge tone={u.active ? 'good' : 'muted'}>{u.active ? 'Active' : 'Off'}</Badge>
                      </td>
                      <td className="py-1.5 text-right">
                        {u.id !== me.id && (
                          <Button variant="quiet" className="!px-2 !py-1 text-xs" onClick={() => toggle(u)}>
                            {u.active ? 'Switch off' : 'Switch on'}
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <NewUser units={units.data ?? []} onCreated={users.reload} />
            </>
          )}
        </Async>
      </Card>
    </div>
  )
}
