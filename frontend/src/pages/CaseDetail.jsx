import { Suspense, lazy, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getBrief, getCase, getGraph, getMembers } from '../api'
import AssignControl, { AssignmentBadge } from '../components/AssignControl'
import Async from '../components/Async'
import Brief from '../components/Brief'
import DetectorChips from '../components/DetectorChips'
import EvidenceList from '../components/EvidenceList'
import OutboundPanel from '../components/OutboundPanel'
import ReviewPanel, { AiRecommendation } from '../components/ReviewPanel'
import { CaseReportButton } from '../components/ReportButtons'
import RiskPanel from '../components/RiskPanel'
import Timeline from '../components/Timeline'
import { Badge, Card, ConfidenceBadge, Skeleton, StatusBadge } from '../components/ui'
import { useAuth } from '../lib/authContext'
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

/** Who holds the case, the team lead's assignment control, and the report download (when allowed). */
function AssignmentPanel({ detail, onChanged }) {
  const { user } = useAuth()
  const access = detail.access
  const members = useApi(
    (signal) => (access?.can_assign ? getMembers(user.unit_id, signal) : Promise.resolve([])),
    [access?.can_assign, user.unit_id],
  )
  if (!access) return null
  return (
    <section aria-labelledby="assignment-title" data-testid="assignment-panel" className="rounded-xl border border-line bg-surface p-4">
      <h2 id="assignment-title" className="text-sm font-semibold text-ink">
        Assignment
      </h2>
      <p className="mt-2 text-sm text-ink-2" data-testid="assignment-summary">
        {access.unit_name ?? 'Not routed to a unit'} · {access.assignee_name ? `with ${access.assignee_name}` : 'not assigned yet'}
      </p>
      <div className="mt-2">
        <AssignmentBadge status={access.assignment_status} />
      </div>
      {access.can_assign && members.data && (
        <div className="mt-3">
          <AssignControl caseId={detail.case_id} members={members.data} assigneeId={access.assignee_id} onDone={onChanged} />
        </div>
      )}
      {access.can_report && (
        <div className="mt-3">
          <CaseReportButton caseId={detail.case_id} />
        </div>
      )}
    </section>
  )
}

function NoDecisionRights() {
  return (
    <section data-testid="read-only-note" className="rounded-xl border border-line bg-surface p-4 text-sm text-ink-2">
      <p className="font-medium text-ink">You are viewing this case</p>
      <p className="mt-1 text-xs">
        Only the assigned investigator or the unit&apos;s team lead can record a decision or draft messages for it.
      </p>
    </section>
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
                  <AssignmentPanel key={`assign-${d.case_id}-${version}`} detail={d} onChanged={() => setVersion((v) => v + 1)} />
                  {d.access?.can_decide === false ? (
                    <>
                      <AiRecommendation detail={d} />
                      <NoDecisionRights />
                    </>
                  ) : (
                    <ReviewPanel key={d.case_id} detail={d} onChanged={() => setVersion((v) => v + 1)} />
                  )}
                  {d.access?.can_outbound !== false && <OutboundPanel key={`out-${d.case_id}`} detail={d} />}
                </aside>
              </div>
            </>
          )
        }}
      </Async>
    </div>
  )
}
