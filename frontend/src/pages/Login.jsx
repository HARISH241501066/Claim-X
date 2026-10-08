import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { errorMessage } from '../api'
import { Button } from '../components/ui'
import { useAuth } from '../lib/authContext'
import { HOME } from '../lib/roles'

const field = 'mt-1 w-full rounded-md border border-axis bg-page px-2 py-2 text-sm text-ink placeholder:text-muted'

export default function Login() {
  const { user, signIn, notice } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to={HOME[user.role] ?? '/'} replace />

  async function submit(event) {
    event.preventDefault()
    if (!username.trim() || !password) {
      setError('Enter your username and password.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await signIn(username.trim(), password)
    } catch (err) {
      setError(errorMessage(err))
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl border border-line bg-surface p-6" aria-labelledby="login-title">
        <h1 id="login-title" className="text-lg font-semibold text-ink">
          ClaimShield Nexus
        </h1>
        <p className="mt-1 text-xs text-muted">Review workbench · synthetic data. Sign in to continue.</p>
        {notice && (
          <p role="status" className="mt-3 rounded-md border border-warn/50 bg-warn/10 px-2 py-1.5 text-xs text-ink">
            {notice}
          </p>
        )}
        <label htmlFor="username" className="mt-4 block text-xs font-medium text-ink-2">
          Username
          <input id="username" className={field} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus />
        </label>
        <label htmlFor="password" className="mt-3 block text-xs font-medium text-ink-2">
          Password
          <input id="password" type="password" className={field} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && (
          <p role="alert" data-testid="login-error" className="mt-3 text-xs text-flag">
            {error}
          </p>
        )}
        <Button variant="primary" type="submit" disabled={busy} className="mt-4 w-full">
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>
        <p className="mt-3 text-xs text-muted">The system recommends. A person decides every case.</p>
      </form>
    </main>
  )
}
