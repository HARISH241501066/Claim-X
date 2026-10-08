import { Link, Navigate, Route, Routes } from 'react-router-dom'
import Shell from './components/Shell'
import { AuthProvider } from './lib/auth'
import { useAuth } from './lib/authContext'
import { HOME } from './lib/roles'
import Admin from './pages/Admin'
import CaseDetail from './pages/CaseDetail'
import Login from './pages/Login'
import Overview from './pages/Overview'
import Queue from './pages/Queue'
import Workload from './pages/Workload'

function NotFound() {
  return (
    <div className="mx-auto max-w-xl rounded-xl border border-line bg-surface p-6">
      <h1 className="text-lg font-semibold text-ink">Page not found</h1>
      <p className="mt-2 text-sm text-ink-2">That address does not match any screen.</p>
      <Link to="/" className="mt-3 inline-block text-sm text-accent hover:underline">
        Go to your start page
      </Link>
    </div>
  )
}

/** Sends a user away from a screen their role does not have. The API refuses it too. */
function Only({ roles, children }) {
  const { user } = useAuth()
  return roles.includes(user.role) ? children : <Navigate to={HOME[user.role]} replace />
}

function Home() {
  const { user } = useAuth()
  return user.role === 'admin' ? <Overview /> : <Navigate to={HOME[user.role]} replace />
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="login" element={<Login />} />
        <Route element={<Shell />}>
          <Route index element={<Home />} />
          <Route path="queue" element={<Only roles={['admin']}><Queue mode="all" /></Only>} />
          <Route path="my-cases" element={<Only roles={['investigator']}><Queue mode="mine" /></Only>} />
          <Route path="unit-queue" element={<Only roles={['investigator', 'team_lead']}><Queue mode="unit" /></Only>} />
          <Route path="workload" element={<Only roles={['team_lead']}><Workload /></Only>} />
          <Route path="system" element={<Only roles={['admin']}><Admin /></Only>} />
          <Route path="cases/:caseId" element={<CaseDetail />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </AuthProvider>
  )
}
