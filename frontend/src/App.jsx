import { useEffect, useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import axios from 'axios'

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

function Home() {
  const [status, setStatus] = useState('checking')

  useEffect(() => {
    axios
      .get(`${API_URL}/health`)
      .then((res) => setStatus(res.data.status === 'ok' ? 'ok' : 'unavailable'))
      .catch(() => setStatus('unavailable'))
  }, [])

  return (
    <main className="mx-auto max-w-2xl p-8">
      <h1 className="text-3xl font-bold text-slate-900">ClaimShield Nexus</h1>
      <p className="mt-2 text-slate-600">
        Synthetic-data FWA review workbench. The system recommends; humans decide.
      </p>
      <p className="mt-6 text-sm" data-testid="api-status">
        API:{' '}
        {status === 'checking' && 'checking...'}
        {status === 'ok' && 'ok'}
        {status === 'unavailable' && 'Insufficient data (API unreachable)'}
      </p>
    </main>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
    </Routes>
  )
}
