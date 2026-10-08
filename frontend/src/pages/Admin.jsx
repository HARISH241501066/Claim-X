import { useState } from 'react'
import { errorMessage, postTestEmail } from '../api'
import { Button, Card } from '../components/ui'

/** Admin tools. For now: check that the urgent-case email reaches the SIU team's inbox. */
export default function Admin() {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  async function send() {
    setBusy(true)
    setResult(null)
    try {
      setResult(await postTestEmail())
    } catch (err) {
      setResult({ ok: false, status: 'failed', detail: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="mb-4 text-xl font-semibold text-ink md:text-2xl">Settings</h1>
      <Card
        title="Email to the SIU team"
        subtitle="Only high-priority cases send an email. It holds a case ID, rank, detector count and a link, nothing else."
      >
        <p className="text-sm text-ink-2">
          Send one test message through the same channel. It contains no case data.
        </p>
        <div className="mt-3">
          <Button variant="primary" disabled={busy} onClick={send}>
            {busy ? 'Sending…' : 'Send test email'}
          </Button>
        </div>
        {result && (
          <p
            role={result.ok ? 'status' : 'alert'}
            data-testid="test-email-result"
            className={`mt-3 text-sm ${result.ok ? 'text-good' : 'text-flag'}`}
          >
            {result.ok ? 'Success. ' : 'Not sent. '}
            {result.detail}
          </p>
        )}
      </Card>
    </div>
  )
}
