import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import App from '../App'
import {
  accessData, briefData, caseDetail, evidenceRows, graphData, healthData, overviewData, providerItem, queueData,
  queueItem, userFor,
} from '../test/fixtures'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal()),
  login: vi.fn(),
  getMe: vi.fn(),
  getQueue: vi.fn(),
  getOverview: vi.fn(),
  getHealth: vi.fn(),
  getCase: vi.fn(),
  getGraph: vi.fn(),
  getBrief: vi.fn(),
  getEvidence: vi.fn(),
  getNotifications: vi.fn(),
  getOutbound: vi.fn(),
  getMembers: vi.fn(),
  getWorkload: vi.fn(),
  postAssign: vi.fn(),
  postUnassign: vi.fn(),
  downloadCaseReport: vi.fn(),
  downloadUnitReport: vi.fn(),
  getUsers: vi.fn(),
  getUnits: vi.fn(),
  getUnrouted: vi.fn(),
}))
vi.mock('../components/NetworkGraph', () => ({ default: () => <div data-testid="graph-stub" /> }))
vi.mock('../components/FindingsChart', () => ({ default: () => <div data-testid="chart-stub" /> }))

const rejection = (status, detail) => ({ response: { status, data: { detail } } })
const members = [
  { id: 3, display_name: 'Arjun Nair', role: 'investigator', unit_id: 1 },
  { id: 4, display_name: 'Divya Reddy', role: 'investigator', unit_id: 1 },
]

function open(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

/** Start as a signed-in user of this role (the token is in sessionStorage, as after a reload). */
function signedInAs(role) {
  api.setToken('test-token')
  api.getMe.mockResolvedValue(userFor(role))
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  api.setToken(null)
  api.getQueue.mockResolvedValue(queueData())
  api.getOverview.mockResolvedValue(overviewData())
  api.getHealth.mockResolvedValue(healthData())
  api.getCase.mockResolvedValue(caseDetail())
  api.getGraph.mockResolvedValue(graphData())
  api.getBrief.mockResolvedValue(briefData())
  api.getEvidence.mockResolvedValue(evidenceRows())
  api.getNotifications.mockResolvedValue({ notifications: [], unread_count: 0 })
  api.getOutbound.mockResolvedValue([])
  api.getMembers.mockResolvedValue(members)
  api.getUsers.mockResolvedValue([])
  api.getUnits.mockResolvedValue([])
  api.getUnrouted.mockResolvedValue([])
})

describe('signing in', () => {
  it('sends someone who is not signed in to the login page', async () => {
    open('/queue')
    expect(await screen.findByRole('heading', { name: 'Claim-X' })).toBeInTheDocument()
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
    expect(api.getQueue).not.toHaveBeenCalled()
  })

  it('shows the API’s message for a wrong password and keeps the password field empty', async () => {
    api.login.mockRejectedValue(rejection(401, 'Wrong username or password.'))
    open('/login')
    await userEvent.type(screen.getByLabelText('Username'), 'south_lead')
    await userEvent.type(screen.getByLabelText('Password'), 'nope')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByTestId('login-error')).toHaveTextContent('Wrong username or password.')
    expect(screen.getByLabelText('Password')).toHaveValue('')
    expect(api.getToken()).toBeNull()
  })

  it('asks for both fields before calling the API', async () => {
    open('/login')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(screen.getByTestId('login-error')).toHaveTextContent('Enter your username and password.')
    expect(api.login).not.toHaveBeenCalled()
  })

  it.each([
    ['investigator', 'My Cases'],
    ['team_lead', 'Unit Queue'],
    ['admin', 'Overview'],
  ])('a %s lands on their own start page after signing in', async (role, heading) => {
    api.login.mockResolvedValue({ access_token: 'abc', user: userFor(role) })
    open('/login')
    await userEvent.type(screen.getByLabelText('Username'), 'someone')
    await userEvent.type(screen.getByLabelText('Password'), 'a-password')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
    expect(api.getToken()).toBe('abc')
  })

  it('signing out clears the token and returns to the login page', async () => {
    signedInAs('team_lead')
    open('/unit-queue')
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
    expect(await screen.findByLabelText('Username')).toBeInTheDocument()
    expect(api.getToken()).toBeNull()
  })

  it('a stale token is dropped, with a message, when the API says 401', async () => {
    api.setToken('old')
    api.getMe.mockRejectedValue(rejection(401, 'Your session has expired or is not valid. Sign in again.'))
    open('/queue')
    expect(await screen.findByLabelText('Username')).toBeInTheDocument()
    expect(api.getToken()).toBeNull()
  })
})

describe('navigation by role', () => {
  const links = async () => {
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    return within(nav).getAllByRole('link').map((a) => a.textContent)
  }

  it('an investigator gets My Cases and a read-only Unit Queue', async () => {
    signedInAs('investigator')
    open('/my-cases')
    expect(await links()).toEqual(['My Cases', 'Unit Queue'])
    expect(screen.getByTestId('current-user')).toHaveTextContent('Arjun Nair · Investigator · Unit South')
    await waitFor(() => expect(api.getQueue).toHaveBeenCalledWith(expect.objectContaining({ view: 'mine' }), expect.any(AbortSignal)))
  })

  it('a team lead gets the Unit Queue and Team Workload', async () => {
    signedInAs('team_lead')
    open('/unit-queue')
    expect(await links()).toEqual(['Unit Queue', 'Team Workload'])
    expect(screen.getByTestId('current-user')).toHaveTextContent('Kavya Menon · Team lead · Unit South')
  })

  it('an admin gets Overview, All Cases and System, and no unit', async () => {
    signedInAs('admin')
    open('/')
    expect(await links()).toEqual(['Overview', 'All Cases', 'System'])
    expect(screen.getByTestId('current-user')).toHaveTextContent('System Admin · Admin')
  })

  it.each([
    ['investigator', '/system', 'My Cases'],
    ['investigator', '/workload', 'My Cases'],
    ['team_lead', '/system', 'Unit Queue'],
    ['team_lead', '/queue', 'Unit Queue'],
    ['admin', '/my-cases', 'Overview'],
  ])('a %s who types %s is sent to %s', async (role, path, heading) => {
    signedInAs(role)
    open(path)
    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
  })

  it('an investigator’s unit queue is read-only: only their own case opens', async () => {
    signedInAs('investigator')
    api.getQueue.mockResolvedValue(queueData({
      scheduled: [
        queueItem({ rank: 1, case_id: 'CASE-0001', assignee_id: 3, assignee_name: 'Arjun Nair', assignment_status: 'assigned', can_open: true }),
        providerItem({ rank: 2, case_id: 'CASE-0003', title: 'Upcoding pattern: PRV-005', can_open: false }),
      ],
      backlog: [],
    }))
    open('/unit-queue')
    const rows = await screen.findAllByTestId('queue-row')
    expect(within(rows[0]).getByRole('link', { name: /Referral network/ })).toHaveAttribute('href', '/cases/CASE-0001')
    expect(within(rows[1]).queryByRole('link')).toBeNull()
    expect(within(rows[1]).getByTestId('assignee-cell')).toHaveTextContent('Unassigned')
    expect(within(rows[1]).queryByRole('button', { name: /Assign/ })).toBeNull() // no assignment controls
    expect(screen.getByRole('heading', { name: 'Unit Queue' })).toBeInTheDocument()
  })
})

describe('team lead assignment', () => {
  const leadQueue = () =>
    queueData({
      scheduled: [
        queueItem({ rank: 1, case_id: 'CASE-0001', assignment_status: 'unassigned' }),
        providerItem({ rank: 2, case_id: 'CASE-0003', assignee_id: 3, assignee_name: 'Arjun Nair', assignment_status: 'in_review' }),
      ],
      backlog: [],
    })

  it('splits the unit queue into unassigned and assigned sections', async () => {
    signedInAs('team_lead')
    api.getQueue.mockResolvedValue(leadQueue())
    open('/unit-queue')
    expect(await screen.findByTestId('section-unassigned')).toHaveTextContent('Unassigned (1)')
    expect(screen.getByTestId('section-assigned')).toHaveTextContent('Assigned (1)')
    expect(api.getMembers).toHaveBeenCalledWith(1, expect.any(AbortSignal))
    const rows = screen.getAllByTestId('queue-row')
    expect(within(rows[1]).getByTestId('assignee-cell')).toHaveTextContent('In review')
    expect(within(rows[1]).getByTestId('assignee-cell')).toHaveTextContent('Arjun Nair')
  })

  it('assigns with a required reason and then reloads the queue', async () => {
    signedInAs('team_lead')
    api.getQueue.mockResolvedValue(leadQueue())
    api.postAssign.mockResolvedValue({})
    open('/unit-queue')
    const row = (await screen.findAllByTestId('queue-row'))[0]
    await userEvent.click(within(row).getByRole('button', { name: /Assign CASE-0001/ }))
    const form = within(row).getByTestId('assign-form')
    expect(within(form).getAllByRole('option').map((o) => o.textContent)).toEqual(['Arjun Nair', 'Divya Reddy'])
    await userEvent.click(within(form).getByRole('button', { name: 'Confirm' }))
    expect(within(form).getByTestId('assign-error')).toHaveTextContent('A reason is required')
    expect(api.postAssign).not.toHaveBeenCalled()
    await userEvent.selectOptions(within(form).getByLabelText('Assign to'), 'Divya Reddy')
    await userEvent.type(within(form).getByLabelText('Reason (required)'), 'Arjun is at capacity')
    await userEvent.click(within(form).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(api.postAssign).toHaveBeenCalledWith('CASE-0001', { assignee_user_id: 4, reason: 'Arjun is at capacity' }))
    await waitFor(() => expect(api.getQueue.mock.calls.length).toBeGreaterThan(1))
  })

  it('offers Reassign and Unassign for an assigned case, without the current assignee in the list', async () => {
    signedInAs('team_lead')
    api.getQueue.mockResolvedValue(leadQueue())
    api.postUnassign.mockResolvedValue({})
    open('/unit-queue')
    const row = (await screen.findAllByTestId('queue-row'))[1]
    await userEvent.click(within(row).getByRole('button', { name: /Reassign CASE-0003/ }))
    const form = within(row).getByTestId('assign-form')
    expect(within(form).getAllByRole('option').map((o) => o.textContent)).toEqual(['Divya Reddy'])
    await userEvent.type(within(form).getByLabelText('Reason (required)'), 'Taking it back for review')
    await userEvent.click(within(form).getByRole('button', { name: 'Unassign' }))
    await waitFor(() => expect(api.postUnassign).toHaveBeenCalledWith('CASE-0003', { reason: 'Taking it back for review' }))
  })

  it('shows the API’s refusal when an assignment is not allowed', async () => {
    signedInAs('team_lead')
    api.getQueue.mockResolvedValue(leadQueue())
    api.postAssign.mockRejectedValue(rejection(403, 'Only the team lead of the case’s unit can assign it.'))
    open('/unit-queue')
    const row = (await screen.findAllByTestId('queue-row'))[0]
    await userEvent.click(within(row).getByRole('button', { name: /Assign CASE-0001/ }))
    await userEvent.type(within(row).getByLabelText('Reason (required)'), 'Needs a closer look')
    await userEvent.click(within(row).getByRole('button', { name: 'Confirm' }))
    expect(await within(row).findByTestId('assign-error')).toHaveTextContent('Only the team lead')
  })

  it('shows the team workload with effort against capacity and the unit report buttons', async () => {
    signedInAs('team_lead')
    api.getWorkload.mockResolvedValue({
      unit_id: 1, unit_name: 'Unit South', unassigned: 3, overdue: 1,
      people: [
        { user_id: 3, name: 'Arjun Nair', assigned: 2, in_review: 1, closed: 1, decisions: 2, open_high_priority: 1, effort_hours: 12, capacity_hours: 40 },
        { user_id: 4, name: 'Divya Reddy', assigned: 0, in_review: 0, closed: 0, decisions: 0, open_high_priority: 0, effort_hours: 45, capacity_hours: 40 },
      ],
    })
    api.downloadUnitReport.mockResolvedValue('unit-south-report.pdf')
    open('/workload')
    const cards = await screen.findAllByTestId('workload-card')
    expect(cards).toHaveLength(2)
    expect(within(cards[0]).getByRole('meter')).toHaveAttribute('aria-valuenow', '12')
    expect(cards[0]).toHaveTextContent('12 h of 40 h')
    expect(cards[1]).toHaveTextContent('Over capacity')
    expect(screen.getByTestId('workload-summary')).toHaveTextContent('3 unassigned · 1 overdue')
    await userEvent.click(screen.getByRole('button', { name: 'Download unit report (PDF)' }))
    await userEvent.click(screen.getByRole('button', { name: 'Download unit report (CSV)' }))
    expect(api.downloadUnitReport.mock.calls).toEqual([[1, 'pdf'], [1, 'csv']])
    expect(await screen.findByTestId('download-message')).toHaveTextContent('Downloaded unit-south-report.pdf')
  })
})

describe('what a case screen shows', () => {
  it('a user who may decide sees the decision panel, messages and the report button', async () => {
    signedInAs('investigator')
    api.downloadCaseReport.mockResolvedValue('CASE-0001-report.pdf')
    open('/cases/CASE-0001')
    expect(await screen.findByTestId('decision-panel')).toBeInTheDocument()
    expect(screen.getByTestId('outbound-panel')).toBeInTheDocument()
    expect(screen.getByTestId('assignment-summary')).toHaveTextContent('Unit South · with Arjun Nair')
    expect(screen.queryByTestId('assign-form')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Download case report (PDF)' }))
    expect(api.downloadCaseReport).toHaveBeenCalledWith('CASE-0001')
    expect(await screen.findByTestId('download-message')).toHaveTextContent('Downloaded CASE-0001-report.pdf')
  })

  it('a read-only viewer sees the recommendation but no decision, messages or assignment controls', async () => {
    signedInAs('admin')
    api.getCase.mockResolvedValue(caseDetail({ access: accessData({ can_decide: false, can_outbound: false, can_assign: false }) }))
    open('/cases/CASE-0001')
    expect(await screen.findByTestId('ai-panel')).toBeInTheDocument()
    expect(screen.getByTestId('read-only-note')).toBeInTheDocument()
    expect(screen.queryByTestId('decision-panel')).toBeNull()
    expect(screen.queryByTestId('outbound-panel')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull()
  })

  it('hides the report button when the user may not download it, and shows the lead’s assign control', async () => {
    signedInAs('team_lead')
    api.getCase.mockResolvedValue(caseDetail({ access: accessData({ can_report: false, can_assign: true, assignee_id: null, assignee_name: null, assignment_status: 'unassigned' }) }))
    open('/cases/CASE-0001')
    expect(await screen.findByTestId('assignment-panel')).toHaveTextContent('not assigned yet')
    expect(screen.queryByRole('button', { name: 'Download case report (PDF)' })).toBeNull()
    expect(await screen.findByRole('button', { name: /Assign CASE-0001/ })).toBeInTheDocument()
  })

  it('shows a clear message when the API refuses the case (403)', async () => {
    signedInAs('investigator')
    api.getCase.mockRejectedValue(rejection(403, 'You do not have access to this case.'))
    open('/cases/CASE-0099')
    expect(await screen.findByRole('alert')).toHaveTextContent('You do not have access to this case.')
  })

  it('shows the refusal when a report download is not allowed', async () => {
    signedInAs('investigator')
    api.downloadCaseReport.mockRejectedValue(rejection(403, 'x'))
    open('/cases/CASE-0001')
    await userEvent.click(await screen.findByRole('button', { name: 'Download case report (PDF)' }))
    expect(await screen.findByTestId('download-message')).toHaveTextContent('You are not allowed to download this report.')
  })
})
