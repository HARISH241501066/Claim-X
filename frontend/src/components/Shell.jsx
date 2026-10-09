import { Navigate, NavLink, Outlet, useLocation } from 'react-router-dom'
import { Button } from './ui'
import { useAuth } from '../lib/authContext'
import { ROLE_LABELS, navFor } from '../lib/roles'
import ErrorBoundary from './ErrorBoundary'
import NotificationBell from './NotificationBell'
import ThemeToggle from './ThemeToggle'

const ICONS = {
  '/': 'M3 10.5 10 4l7 6.5V16a1 1 0 0 1-1 1h-3.5v-4.5h-5V17H4a1 1 0 0 1-1-1v-5.5Z',
  '/queue': 'M4 5h12M4 10h12M4 15h12',
  '/my-cases': 'M5 3.5h10a1 1 0 0 1 1 1V17l-3-2-3 2-3-2-3 2V4.5a1 1 0 0 1 1-1Z',
  '/unit-queue': 'M4 5h12M4 10h8M4 15h5',
  '/workload': 'M5 16V9m5 7V4m5 12v-5',
  '/system': 'M10 6.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM10 2v2m0 12v2M2 10h2m12 0h2M4.3 4.3l1.4 1.4m8.6 8.6 1.4 1.4m0-11.4-1.4 1.4M5.7 14.3l-1.4 1.4',
}

function NavIcon({ to }) {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="shrink-0">
      <path d={ICONS[to] ?? ICONS['/queue']} />
    </svg>
  )
}

function Logo() {
  return (
    <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent text-white shadow-sm">
      <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10 2.5 16.5 5v4.7c0 3.7-2.6 6.3-6.5 7.8-3.9-1.5-6.5-4.1-6.5-7.8V5L10 2.5Z" />
        <path d="m7 10 2.2 2.2L13.2 8" />
      </svg>
    </span>
  )
}

export default function Shell() {
  const { user, checking, signOut } = useAuth()
  const location = useLocation()
  if (checking) return <p className="p-8 text-sm text-muted">Checking your session…</p>
  if (!user) return <Navigate to="/login" replace />

  const initial = (user.display_name || user.username || '?').trim().charAt(0).toUpperCase()

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-accent px-3 py-2 text-sm font-medium text-white focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>
      <aside className="themed shrink-0 border-b border-line bg-surface/90 backdrop-blur md:sticky md:top-0 md:h-screen md:w-60 md:border-b-0 md:border-r">
        <div className="flex items-center gap-3 px-5 py-5">
          <Logo />
          <div className="min-w-0">
            <p className="text-base font-semibold leading-tight tracking-tight text-ink">ClaimShield Nexus</p>
            <p className="mt-0.5 text-xs text-muted">Review workbench · synthetic data</p>
          </div>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col">
          {navFor(user.role).map(({ to, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `relative flex items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-accent/15 text-ink before:absolute before:inset-y-1.5 before:left-0 before:w-0.5 before:rounded-full before:bg-accent'
                    : 'text-ink-2 hover:bg-surface-2 hover:text-ink'
                }`
              }
            >
              <NavIcon to={to} />
              {label}
            </NavLink>
          ))}
        </nav>
        <p className="mx-4 mt-6 hidden rounded-lg border border-line bg-surface-2/60 p-3 text-xs leading-relaxed text-muted md:block">
          The system recommends. A person decides every case.
        </p>
      </aside>
      <main id="main" tabIndex={-1} className="min-w-0 flex-1 px-4 pb-10 md:px-8">
        <div
          className="themed sticky top-0 z-20 -mx-4 mb-5 flex flex-wrap items-center justify-end gap-3 border-b border-line bg-page/80 px-4 py-3 backdrop-blur md:-mx-8 md:px-8"
          data-testid="top-bar"
        >
          <div className="flex items-center gap-2.5">
            <span aria-hidden="true" className="flex h-8 w-8 items-center justify-center rounded-full bg-accent/15 text-sm font-semibold text-accent">
              {initial}
            </span>
            <p className="text-xs text-ink-2" data-testid="current-user">
              <span className="font-medium text-ink">{user.display_name}</span> · {ROLE_LABELS[user.role]}
              {user.unit_name && ` · ${user.unit_name}`}
            </p>
          </div>
          <ThemeToggle />
          <NotificationBell />
          <Button variant="quiet" onClick={() => signOut()}>
            Sign out
          </Button>
        </div>
        <ErrorBoundary>
          <div key={location.pathname} className="rise-in">
            <Outlet />
          </div>
        </ErrorBoundary>
      </main>
    </div>
  )
}
