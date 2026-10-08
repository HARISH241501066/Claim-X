import { Link, Route, Routes } from 'react-router-dom'
import Shell from './components/Shell'
import CaseDetail from './pages/CaseDetail'
import Overview from './pages/Overview'
import Queue from './pages/Queue'

function NotFound() {
  return (
    <div className="mx-auto max-w-xl rounded-xl border border-line bg-surface p-6">
      <h1 className="text-lg font-semibold text-ink">Page not found</h1>
      <p className="mt-2 text-sm text-ink-2">That address does not match any screen.</p>
      <Link to="/" className="mt-3 inline-block text-sm text-accent hover:underline">
        Go to the overview
      </Link>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Overview />} />
        <Route path="queue" element={<Queue />} />
        <Route path="cases/:caseId" element={<CaseDetail />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
