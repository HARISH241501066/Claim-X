import { Navigate, NavLink, Outlet } from 'react-router-dom'
import { Button } from './ui'
import { useAuth } from '../lib/authContext'
import { ROLE_LABELS, navFor } from '../lib/roles'
import ErrorBoundary from './ErrorBoundary'
import NotificationBell from './NotificationBell'

export default function Shell() {
  const { user, checking, signOut } = useAuth()
  if (checking) return <p className="p-8 text-sm text-muted">Checking your session…</p>
  if (!user) return <Navigate to="/login" replace />

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="shrink-0 border-b border-line bg-surface md:min-h-screen md:w-56 md:border-b-0 md:border-r">
        <div className="px-5 py-5">
          <p className="text-base font-semibold tracking-tight text-ink">ClaimShield Nexus</p>
          <p className="mt-1 text-xs text-muted">Review workbench · synthetic data</p>
        </div>
        <nav aria-label="Main" className="flex gap-1 px-3 pb-3 md:flex-col">
          {navFor(user.role).map(({ to, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `rounded-md px-3 py-2 text-sm font-medium ${
                  isActive ? 'bg-accent/15 text-ink' : 'text-ink-2 hover:bg-surface-2'
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <p className="hidden px-5 pt-6 text-xs leading-relaxed text-muted md:block">
          The system recommends. A person decides every case.
        </p>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-4 md:px-8">
        <div className="mb-4 flex flex-wrap items-center justify-end gap-3 border-b border-line pb-3" data-testid="top-bar">
          <p className="text-xs text-ink-2" data-testid="current-user">
            <span className="font-medium text-ink">{user.display_name}</span> · {ROLE_LABELS[user.role]}
            {user.unit_name && ` · ${user.unit_name}`}
          </p>
          <NotificationBell />
          <Button variant="quiet" onClick={() => signOut()}>
            Sign out
          </Button>
        </div>
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  )
}
