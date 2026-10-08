import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import App from '../App'
import {
  briefData, caseDetail, evidenceRows, graphData, healthData, overviewData, queueData, userFor,
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
  getUsers: vi.fn(),
  getUnits: vi.fn(),
  getUnrouted: vi.fn(),
  postRerun: vi.fn(),
  postPrewarm: vi.fn(),
}))
vi.mock('../components/NetworkGraph', () => ({ default: () => <div data-testid="graph-stub" /> }))
vi.mock('../components/FindingsChart', () => ({ default: () => <div data-testid="chart-stub" /> }))

const BANNED = ['fraud' + ' probability', 'chance of ' + 'fraud']
const noBannedWords = () => BANNED.forEach((p) => expect(document.body.textContent.toLowerCase()).not.toContain(p))
const network = { response: undefined, code: 'ERR_NETWORK', message: 'Network Error' }
const rejection = (detail) => ({ response: { status: 422, data: { detail } } })

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
  api.getUsers.mockResolvedValue([userFor('admin')])
  api.getUnits.mockResolvedValue([{ id: 1, name: 'Unit South', region: ['Chennai'] }])
  api.getUnrouted.mockResolvedValue([])
})

const note = (over = {}) => ({
  id: 1, recipient_role: 'siu', type: 'new_case', severity: 'info', case_id: 'CASE-0003',
  message: 'New case CASE-0003 (provider) awaits review: 1 of 3 detectors agree, queue rank 4.',
  created_at: '2026-10-08T09:00:00Z', read: false, email_status: 'not_required', ...over,
})
const bellData = () => ({
  unread_count: 3,
  notifications: [
    note({ id: 3, severity: 'high', type: 'high_priority', case_id: 'CASE-0001', email_status: 'sent', message: 'High priority: CASE-0001 (rank 1) — a critical finding.' }),
    note({ id: 2, severity: 'warning', type: 'capacity', case_id: null, message: 'Capacity exceeded: 3 cases in backlog.' }),
    note({ id: 1 }),
  ],
})

describe('notification bell', () => {
  it('shows the unread count and groups the list with high priority first', async () => {
    api.getNotifications.mockResolvedValue(bellData())
    open('/queue')
    expect(await screen.findByTestId('unread-count')).toHaveTextContent('3')
    expect(api.getNotifications).toHaveBeenCalledWith({}, expect.any(AbortSignal))
    await userEvent.click(screen.getByRole('button', { name: /Notifications, 3 unread/ }))
    const panel = screen.getByTestId('notification-panel')
    const headings = within(panel).getAllByRole('heading', { level: 3 }).map((h) => h.textContent.replace(/\s*\d+$/, ''))
    expect(headings).toEqual(['High priority', 'Warnings', 'Information'])
    const items = within(panel).getAllByTestId('notification')
    expect(items.map((i) => i.dataset.severity)).toEqual(['high', 'warning', 'info'])
    expect(items[0]).toHaveTextContent('High priority: CASE-0001')
    expect(within(items[0]).getByTestId('email-sent')).toBeInTheDocument() // the envelope
    expect(within(items[2]).queryByTestId('email-sent')).toBeNull()
    expect(within(items[0]).getByRole('link')).toHaveAttribute('href', '/cases/CASE-0001')
  })

  it('marks an opened item read, and "Mark all read" asks the server to clear your own notifications', async () => {
    api.getNotifications.mockResolvedValue(bellData())
    api.postNotificationRead.mockResolvedValue({})
    api.postReadAll.mockResolvedValue({ marked: 3 })
    open('/queue')
    await userEvent.click(await screen.findByRole('button', { name: /Notifications, 3 unread/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Mark all read' }))
    expect(api.postReadAll).toHaveBeenCalledTimes(1)
    await userEvent.click(within(screen.getByTestId('notification-panel')).getAllByRole('link')[0])
    expect(api.postNotificationRead).toHaveBeenCalledWith(3)
  })

  it('shows no count when everything is read', async () => {
    open('/queue')
    await screen.findAllByTestId('queue-row')
    expect(screen.queryByTestId('unread-count')).toBeNull()
    expect(screen.queryByLabelText('Viewing as')).toBeNull() // there is no role switch: you see your own inbox
  })

  it('does not break the page when the notifications cannot be loaded', async () => {
    api.getNotifications.mockRejectedValue(network)
    open('/queue')
    expect(await screen.findAllByTestId('queue-row')).not.toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: /Notifications/ }))
    expect(screen.getByTestId('notification-panel')).toBeInTheDocument()
  })
})

describe('system page', () => {
  it('sends a test email and shows the result, or the error', async () => {
    api.postTestEmail.mockResolvedValueOnce({ ok: true, status: 'sent', detail: 'Test email published to the SNS topic.' })
    open('/system')
    const card = (await screen.findByRole('button', { name: 'Send test email' })).closest('section')
    await userEvent.click(within(card).getByRole('button', { name: 'Send test email' }))
    expect(await within(card).findByTestId('task-result')).toHaveTextContent('Success. Test email published')
    api.postTestEmail.mockResolvedValueOnce({ ok: false, status: 'failed', detail: 'Sending failed: NoCredentialsError.' })
    await userEvent.click(within(card).getByRole('button', { name: 'Send test email' }))
    await waitFor(() =>
      expect(within(card).getByTestId('task-result')).toHaveTextContent('Not sent. Sending failed: NoCredentialsError.'),
    )
  })
})

describe('messages to providers and members', () => {
  const draft = (over = {}) => ({
    id: 7, case_id: 'CASE-0001', recipient_type: 'provider', recipient_id: 'PRV-A01',
    template: 'records_request', subject: 'Request for records',
    body: 'As part of a routine documentation review, please submit records for the following claims: CLM-1 (2026-01-02). Kindly respond within 15 days.',
    status: 'draft', created_by: 'Asha Rao', approved_by: null, created_at: '2026-10-08T09:00:00Z', sent_at: null, ...over,
  })

  it('creates a draft with the notice and lists it in the history with a status badge', async () => {
    api.postOutbound.mockResolvedValue(draft())
    api.getOutbound.mockResolvedValueOnce([]).mockResolvedValue([draft()])
    open('/cases/CASE-0001')
    await userEvent.click(await screen.findByRole('button', { name: 'Request records' }))
    expect(api.postOutbound).toHaveBeenCalledWith('CASE-0001', {
      template: 'records_request', recipient_type: 'provider', recipient_id: 'PRV-A01',
    })
    const box = await screen.findByTestId('outbound-draft')
    expect(within(box).getByRole('note')).toHaveTextContent(
      'This message will not be sent until you approve it. Do not mention suspicion or investigation.',
    )
    expect(within(box).getByLabelText('Message')).toHaveValue(draft().body)
    expect(within(screen.getByTestId('outbound-history')).getByText('Draft')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verify with member' })).toBeInTheDocument()
  })

  it('shows the validator error inline, and approval needs an approver and a reason', async () => {
    api.getOutbound.mockResolvedValue([draft()])
    api.putOutbound.mockRejectedValueOnce(
      rejection('This wording is not allowed in a message to a provider or member: “fraud”. Remove it and try again.'),
    )
    open('/cases/CASE-0001')
    await userEvent.click(await screen.findByRole('button', { name: 'Edit draft' }))
    const box = screen.getByTestId('outbound-draft')
    await userEvent.click(within(box).getByRole('button', { name: /Approve & send/ }))
    expect(within(box).getByTestId('approver')).toHaveTextContent('You approve as System Admin')
    expect(within(box).getByTestId('outbound-message')).toHaveTextContent('A reason is required')
    expect(api.postApprove).not.toHaveBeenCalled()
    await userEvent.type(within(box).getByLabelText(/Reason for approving/), 'Wording checked')
    await userEvent.click(within(box).getByRole('button', { name: /Approve & send/ }))
    expect(await within(box).findByText(/not allowed/)).toBeInTheDocument()
    expect(api.postApprove).not.toHaveBeenCalled() // the failed edit stopped the approval
  })

  it('approves with the approver and reason, then shows it as sent (simulated)', async () => {
    api.getOutbound
      .mockResolvedValueOnce([draft()])
      .mockResolvedValue([draft({ status: 'sent_simulated', approved_by: 'Asha Rao', sent_at: '2026-10-08T10:00:00Z' })])
    api.putOutbound.mockResolvedValue(draft())
    api.postApprove.mockResolvedValue(draft({ status: 'sent_simulated' }))
    open('/cases/CASE-0001')
    await userEvent.click(await screen.findByRole('button', { name: 'Edit draft' }))
    const box = screen.getByTestId('outbound-draft')
    await userEvent.type(within(box).getByLabelText(/Reason for approving/), 'Wording checked')
    await userEvent.click(within(box).getByRole('button', { name: /Approve & send/ }))
    await waitFor(() =>
      expect(api.postApprove).toHaveBeenCalledWith(7, { reason: 'Wording checked' }),
    )
    expect(await screen.findByText('Sent (simulated)')).toBeInTheDocument()
    expect(screen.queryByTestId('outbound-draft')).toBeNull()
    noBannedWords()
  })

  it('shows the server message when a draft cannot be created', async () => {
    api.postOutbound.mockRejectedValue(rejection('That provider is not part of this case.'))
    open('/cases/CASE-0001')
    await userEvent.click(await screen.findByRole('button', { name: 'Request records' }))
    expect(await screen.findByTestId('outbound-error')).toHaveTextContent('That provider is not part of this case.')
  })
})
