import { Suspense, lazy, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getBrief, getCase, getGraph } from '../api'
import Async from '../components/Async'
import Brief from '../components/Brief'
import DetectorChips from '../components/DetectorChips'
import EvidenceList from '../components/EvidenceList'
import ReviewPanel from '../components/ReviewPanel'
import RiskPanel from '../components/RiskPanel'
import Timeline from '../components/Timeline'
import { Badge, Card, ConfidenceBadge, Skeleton, StatusBadge } from '../components/ui'
import { priority as fmtPriority, rupees } from '../lib/format'
import { useApi } from '../lib/useApi'

const NetworkGraph = lazy(() => import('../components/NetworkGraph'))

function Header({ detail }) {
  return (
    <header className="mb-5">
      <Link to="/queue" className="text-xs text-accent underline-offset-2 hover:underline">
        ← Back to the queue
      </Link>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold text-ink md:text-2xl">{detail.title}</h1>
        <Badge tone={detail.case_type === 'ring' ? 'critical' : 'neutral'}>
          {detail.case_type === 'ring' ? 'Ring' : 'Provider'}
        </Badge>
        <StatusBadge status={detail.status} />
      </div>
      <p className="mt-1 text-xs text-muted">
        {detail.case_id} · rank {detail.rank} · {detail.queue} · {rupees(detail.flagged_amount)} flagged ·{' '}
        {detail.affected_members.length} members
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <span className="text-sm text-ink" data-testid="header-priority">
          Priority <strong className="tabular-nums">{fmtPriority(detail.priority)}</strong>
          {detail.override && (
            <span className="ml-2 text-xs text-warn">(AI {fmtPriority(detail.ai_priority)}, overridden)</span>
          )}
        </span>
        <span className="text-sm text-ink" data-testid="header-detectors">
          {detail.detectors_fired.length} of 3 detectors
        </span>
        <DetectorChips fired={detail.detectors_fired} />
        <ConfidenceBadge level={detail.confidence?.level} reasons={detail.confidence?.reasons} />
      </div>
    </header>
  )
}

export default function CaseDetail() {
  const { caseId } = useParams()
  const [version, setVersion] = useState(0)
  const [selectedKey, setSelectedKey] = useState(null)
  const [horizon, setHorizon] = useState(30)

  const detail = useApi((signal) => getCase(caseId, signal), [caseId, version])
  const graph = useApi((signal) => getGraph(caseId, signal), [caseId])
  const brief = useApi((signal) => getBrief(caseId, 30, signal), [caseId])

  return (
    <div className="mx-auto max-w-7xl">
      <Async state={detail} label="Loading the case" rows={6}>
        {(d) => {
          const validKeys = new Set(d.findings.map((f) => f.key))
          const cite = (key) => setSelectedKey(key)
          return (
            <>
              <Header detail={d} />
              <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
                <div className="min-w-0 space-y-6">
                  <Card title="Evidence" subtitle="Each finding is keyed E1, E2 … and cites the records behind it">
                    <EvidenceList
                      caseId={d.case_id}
                      findings={d.findings}
                      selectedKey={selectedKey}
                      onSelect={setSelectedKey}
                    />
                  </Card>

                  <Card title="Network" subtitle="Entities and the members linked to the flagged claims">
                    <Async state={graph} label="Loading the network" rows={5}>
                      {(g) => (
                        <Suspense fallback={<Skeleton rows={5} label="Loading the network" />}>
                          <NetworkGraph graph={g} />
                        </Suspense>
                      )}
                    </Async>
                  </Card>

                  <Card title="Timeline" subtitle="Dated events taken from the evidence">
                    <Timeline entries={d.timeline} onCite={cite} />
                  </Card>

                  <Card title="Brief" subtitle="Written from the evidence only; citations open the evidence above">
                    <Async state={brief} label="Preparing the brief" rows={6}>
                      {(b) => <Brief brief={b} validKeys={validKeys} onCite={cite} />}
                    </Async>
                  </Card>
                </div>

                <aside className="min-w-0 space-y-6 lg:sticky lg:top-6 lg:self-start">
                  <RiskPanel prediction={d.prediction} horizon={horizon} onHorizon={setHorizon} />
                  <ReviewPanel key={d.case_id} detail={d} onChanged={() => setVersion((v) => v + 1)} />
                </aside>
              </div>
            </>
          )
        }}
      </Async>
    </div>
  )
}
