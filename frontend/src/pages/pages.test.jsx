import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import App from '../App'
import {
  briefData, caseDetail, userFor, evidenceRows, graphData, healthData, overviewData, providerItem, queueData, queueItem,
} from '../test/fixtures'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getQueue: vi.fn(),
  getOverview: vi.fn(),
  getHealth: vi.fn(),
  getCase: vi.fn(),
  getGraph: vi.fn(),
  getBrief: vi.fn(),
  getEvidence: vi.fn(),
  postDecision: vi.fn(),
  postOverride: vi.fn(),
  getNotifications: vi.fn(),
  postNotificationRead: vi.fn(),
  postReadAll: vi.fn(),
  postTestEmail: vi.fn(),
  getOutbound: vi.fn(),
  postOutbound: vi.fn(),
  putOutbound: vi.fn(),
  postApprove: vi.fn(),
  getMe: vi.fn(),
  getMembers: vi.fn(),
  getWorkload: vi.fn(),
  getUsers: vi.fn(),
  getUnits: vi.fn(),
  getUnrouted: vi.fn(),
  login: vi.fn(),
}))
vi.mock('../components/NetworkGraph', () => ({
  default: ({ graph }) => <div data-testid="graph-stub">{graph.nodes.length} nodes</div>,
}))
vi.mock('../components/FindingsChart', () => ({
  default: ({ data }) => <div data-testid="chart-stub">{Object.keys(data).length} detectors</div>,
}))

const BANNED = ['fraud' + ' probability', 'chance of ' + 'fraud']
const noBannedWords = () => BANNED.forEach((p) => expect(document.body.textContent.toLowerCase()).not.toContain(p))
const network = { response: undefined, code: 'ERR_NETWORK', message: 'Network Error' }

function open(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  api.getQueue.mockResolvedValue(queueData())
  api.getOverview.mockResolvedValue(overviewData())
  api.getHealth.mockResolvedValue(healthData())
  api.getCase.mockResolvedValue(caseDetail())
  api.getGraph.mockResolvedValue(graphData())
  api.getBrief.mockResolvedValue(briefData())
  api.getEvidence.mockResolvedValue(evidenceRows())
  api.getNotifications.mockResolvedValue({ notifications: [], unread_count: 0 })
  api.getOutbound.mockResolvedValue([])
  api.setToken('test-token')
  api.getMe.mockResolvedValue(userFor('admin'))
  api.getMembers.mockResolvedValue([])
})

describe('shell', () => {
  it('has a left navigation with Overview and All Cases, and marks the current page', async () => {
    open('/queue')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Overview' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'All Cases' })).toHaveAttribute('aria-current', 'page')
    await screen.findAllByTestId('queue-row')
  })

  it('shows a not-found page for an unknown address', async () => {
    open('/nowhere')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})

describe('Overview page', () => {
  it('shows the hero amount, the funnel tiles and the chart', async () => {
    open('/')
    expect(await screen.findByTestId('amount-at-risk')).toHaveTextContent('Rs 924,113')
    expect(screen.getByTestId('tile-claims')).toHaveTextContent('5,000')
    expect(screen.getByTestId('tile-findings')).toHaveTextContent('57')
    expect(screen.getByTestId('tile-cases')).toHaveTextContent('20 awaiting review · 0 decided')
    expect(screen.getByTestId('tile-scheduled')).toHaveTextContent('8')
    expect(screen.getByTestId('tile-scheduled')).toHaveTextContent('37 h of 40 h team hours')
    expect(await screen.findByTestId('chart-stub')).toHaveTextContent('6 detectors')
    expect(await screen.findByText(/Pipeline ok · 4.4 s · 1 stages/)).toBeInTheDocument()
    noBannedWords()
  })

  it('shows a loading state first and never a blank page', async () => {
    api.getOverview.mockReturnValue(new Promise(() => {}))
    open('/')
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Loading overview' })).toBeInTheDocument()
  })

  it('shows the error with Try again, then recovers', async () => {
    api.getOverview.mockRejectedValueOnce(network)
    open('/')
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the API')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByTestId('amount-at-risk')).toBeInTheDocument()
  })

  it('still shows the numbers when only the pipeline status fails', async () => {
    api.getHealth.mockRejectedValue(network)
    open('/')
    expect(await screen.findByTestId('amount-at-risk')).toBeInTheDocument()
    expect(await screen.findByText('Pipeline status unavailable')).toBeInTheDocument()
  })
})

describe('Queue page', () => {
  it('asks for the default capacity and weights, then lists the ranked cases', async () => {
    open('/queue')
    const rows = await screen.findAllByTestId('queue-row')
    expect(rows.map((r) => r.dataset.case)).toEqual(['CASE-0001', 'CASE-0003', 'CASE-0005'])
    expect(api.getQueue).toHaveBeenCalledWith(
      { capacity: 40, weights: 'risk:0.3,dollars:0.25,impact:0.15,severity:0.15,evidence:0.15', include_decided: false, view: undefined },
      expect.any(AbortSignal),
    )
    expect(screen.getByTestId('queue-summary')).toHaveTextContent('2 scheduled (13 h of 40 h) · 1 in backlog')
    noBannedWords()
  })

  it('shows the error, then the queue after Try again', async () => {
    api.getQueue.mockRejectedValueOnce(network)
    open('/queue')
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the API')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findAllByTestId('queue-row')).toHaveLength(3)
  })

  it('re-schedules when the capacity slider moves (after a short pause)', async () => {
    open('/queue')
    await screen.findAllByTestId('queue-row')
    fireEvent.change(screen.getByLabelText('Team capacity in hours'), { target: { value: '20' } })
    expect(screen.getByTestId('capacity-value')).toHaveTextContent('20 h')
    await waitFor(() => expect(api.getQueue).toHaveBeenLastCalledWith(expect.objectContaining({ capacity: 20 }), expect.any(AbortSignal)), { timeout: 2000 })
  })

  it('sends weights that add up to exactly 1 and keeps the displayed shares at 100%', async () => {
    open('/queue')
    await screen.findAllByTestId('queue-row')
    await userEvent.click(screen.getByRole('button', { name: /Priority weights/ }))
    fireEvent.change(screen.getByLabelText('Risk weight'), { target: { value: '77' } })
    await waitFor(() => {
      const { weights } = api.getQueue.mock.calls.at(-1)[0]
      expect(weights).not.toContain('risk:0.3,')
    }, { timeout: 2000 })
    const parts = api.getQueue.mock.calls.at(-1)[0].weights.split(',').map((p) => Number(p.split(':')[1]))
    expect(Math.abs(parts.reduce((a, b) => a + b, 0) - 1)).toBeLessThan(1e-9)
    const shown = ['risk', 'dollars', 'impact', 'severity', 'evidence'].map((k) => Number(screen.getByTestId(`weight-${k}`).textContent.replace('%', '')))
    expect(shown.reduce((a, b) => a + b, 0)).toBe(100)
  })

  it('does not ask the server when every weight is zero, and says why', async () => {
    open('/queue')
    await screen.findAllByTestId('queue-row')
    await userEvent.click(screen.getByRole('button', { name: /Priority weights/ }))
    for (const name of ['Risk', 'Dollars', 'Members affected', 'Severity', 'Evidence breadth']) {
      fireEvent.change(screen.getByLabelText(`${name} weight`), { target: { value: '0' } })
    }
    const calls = api.getQueue.mock.calls.length
    expect(screen.getByRole('alert')).toHaveTextContent('at least one weight')
    await new Promise((r) => setTimeout(r, 500))
    expect(api.getQueue.mock.calls.length).toBe(calls)
  })

  it('can include decided cases', async () => {
    open('/queue')
    await screen.findAllByTestId('queue-row')
    await userEvent.click(screen.getByLabelText('Show decided cases'))
    await waitFor(() => expect(api.getQueue).toHaveBeenLastCalledWith(expect.objectContaining({ include_decided: true }), expect.any(AbortSignal)), { timeout: 2000 })
  })

  it('shows a reviewer override next to the AI priority and notes hidden decided cases', async () => {
    api.getQueue.mockResolvedValue(
      queueData({
        scheduled: [providerItem({ rank: 1, priority: 0.99, ai_priority: 0.4, override: { priority: 0.99, reviewer: 'Asha Rao', reason: 'Urgent', ts: 't' } })],
        backlog: [],
        decided_excluded: 2,
      }),
    )
    open('/queue')
    expect(await screen.findByText('AI 0.40 → Asha Rao 0.99')).toBeInTheDocument()
    expect(screen.getByTestId('queue-summary')).toHaveTextContent('2 decided hidden')
  })

  it('opens a case from its title', async () => {
    api.getQueue.mockResolvedValue(queueData({ scheduled: [queueItem()], backlog: [] }))
    open('/queue')
    await userEvent.click(await screen.findByRole('link', { name: /Referral network/ }))
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Referral network')
  })
})

describe('Case detail page', () => {
  it('shows the header, evidence, network, timeline, risk, brief and both review panels', async () => {
    open('/cases/CASE-0001')
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Referral network: RING-01')
    expect(screen.getByTestId('header-detectors')).toHaveTextContent('2 of 3 detectors')
    expect(screen.getByTestId('header-priority')).toHaveTextContent('Priority 0.770')
    expect(screen.getAllByTestId('confidence-badge')[0]).toHaveTextContent('Confidence: Medium') // header
    expect(screen.getAllByTestId('confidence-badge')).toHaveLength(2) // and again in the AI panel
    expect(screen.getByTestId('status-badge')).toHaveTextContent('Awaiting human review')
    expect(screen.getAllByTestId('evidence-item').map((e) => e.id)).toEqual(['evidence-E1', 'evidence-E2'])
    expect(await screen.findByTestId('graph-stub')).toHaveTextContent('3 nodes')
    expect(screen.getAllByTestId('timeline-entry')).toHaveLength(2)
    expect(screen.getByRole('heading', { name: '30-Day Investigation Risk: 2.8%' })).toBeInTheDocument()
    expect(await screen.findByTestId('brief-source')).toHaveTextContent(/^Template$/)
    expect(screen.getByRole('heading', { name: 'AI Recommendation' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Your Decision' })).toBeInTheDocument()
    expect(api.getBrief).toHaveBeenCalledWith('CASE-0001', 30, expect.any(AbortSignal))
    noBannedWords()
  })

  it('shows the claim rows behind an evidence item when it is opened', async () => {
    open('/cases/CASE-0001')
    const e2 = await screen.findAllByTestId('evidence-item').then((items) => items[1])
    await userEvent.click(within(e2).getByRole('button', { name: 'Show claim rows' }))
    expect(api.getEvidence).toHaveBeenCalledWith('CASE-0001', 'E2', expect.any(AbortSignal))
    expect(await within(e2).findAllByTestId('claim-row')).toHaveLength(2)
    expect(within(e2).getByText('Showing 2 of 2 claims.')).toBeInTheDocument()
    await userEvent.click(within(e2).getByRole('button', { name: 'Hide claim rows' }))
    expect(within(e2).queryByTestId('claim-row')).toBeNull()
  })

  it('selects the evidence when a brief citation is clicked', async () => {
    open('/cases/CASE-0001')
    const cite = await screen.findAllByRole('button', { name: '[E2]' })
    await userEvent.click(cite.find((b) => b.dataset.cite))
    const e2 = screen.getAllByTestId('evidence-item')[1]
    expect(e2).toHaveAttribute('aria-current', 'true')
    expect(await within(e2).findAllByTestId('claim-row')).toHaveLength(2)
  })

  it('keeps the rest of the page when the brief fails, and lets the reviewer retry', async () => {
    api.getBrief.mockRejectedValueOnce(network)
    open('/cases/CASE-0001')
    expect(await screen.findByTestId('graph-stub')).toBeInTheDocument()
    const alert = await screen.findByText(/Cannot reach the API/)
    expect(alert).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Your Decision' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByTestId('brief-source')).toBeInTheDocument()
  })

  it('keeps the page when the network graph fails', async () => {
    api.getGraph.mockRejectedValue({ response: { status: 503, data: {} } })
    open('/cases/CASE-0001')
    expect(await screen.findByText(/still starting up/)).toBeInTheDocument()
    expect(screen.getAllByTestId('evidence-item')).toHaveLength(2)
  })

  it('shows an error with retry for an unknown case', async () => {
    api.getCase.mockRejectedValue({ response: { status: 404, data: { detail: 'Unknown case CASE-9999' } } })
    open('/cases/CASE-9999')
    expect(await screen.findByRole('alert')).toHaveTextContent('Unknown case CASE-9999')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('records a decision with a reason, then reloads the case with its new status', async () => {
    api.postDecision.mockResolvedValue({ case_status: 'Escalated for investigation', reviewer: 'Asha Rao' })
    api.getCase.mockResolvedValueOnce(caseDetail()).mockResolvedValue(
      caseDetail({
        status: 'Escalated for investigation',
        decisions: [{ audit_id: 4, action: 'escalate_for_investigation', reason: 'Needs verification', reviewer: 'Asha Rao', ts: '2026-10-08T12:00:00Z' }],
      }),
    )
    open('/cases/CASE-0001')
    await screen.findByRole('heading', { level: 1 })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Open investigation' }))
    expect(screen.getByTestId('decision-error')).toBeInTheDocument()
    expect(api.postDecision).not.toHaveBeenCalled()
    await user.type(screen.getByLabelText('Reason (required)'), 'Needs verification')
    await user.click(screen.getByRole('button', { name: 'Open investigation' }))
    expect(api.postDecision).toHaveBeenCalledWith('CASE-0001', {
      action: 'escalate_for_investigation', reason: 'Needs verification',
    })
    await waitFor(() => expect(screen.getByTestId('status-badge')).toHaveTextContent('Escalated for investigation'))
    expect(screen.getByTestId('decision-confirmation')).toHaveTextContent('Recorded')
    expect(within(screen.getByTestId('decision-history')).getByText(/Needs verification/)).toBeInTheDocument()
    noBannedWords()
  })

  it('shows a ring case and a provider case with the right badge', async () => {
    api.getCase.mockResolvedValue(caseDetail({ case_id: 'CASE-0003', title: 'Upcoding pattern: PRV-005', case_type: 'provider', detectors_fired: ['rules'], confidence: { level: 'Low', reasons: ['1 of 3'] } }))
    open('/cases/CASE-0003')
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Upcoding pattern: PRV-005')
    expect(screen.getByTestId('header-detectors')).toHaveTextContent('1 of 3 detectors')
    expect(screen.getAllByTestId('confidence-badge')[0]).toHaveTextContent('Confidence: Low')
  })
})
