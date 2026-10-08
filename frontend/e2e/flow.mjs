// End-to-end check in a real Chrome, signing in as each seeded role: admin, team lead, investigator.
// It expects the API (with CLAIMSHIELD_AUDIT_PATH set to a throwaway file, DEMO_PASSWORD and
// JWT_SECRET set) and the Vite dev server to be running. DEMO_PASSWORD is read from the environment
// or the repo-root .env and is never printed. It fails if the browser console shows an unexpected
// error or warning.
import fs from 'node:fs'
import { chromium } from 'playwright-core'

const BASE = process.env.BASE_URL ?? 'http://localhost:5173'
const API = process.env.API_URL ?? 'http://localhost:8000'
const CHROME = process.env.CHROME_PATH ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const SHOTS = process.env.SHOT_DIR ?? 'e2e/screenshots'
const BANNED = ['fraud' + ' probability', 'chance of ' + 'fraud', 'fraud' + '_prob', 'fraud' + 'ster', 'guil' + 'ty']
const EXPECT_SOURCE = process.env.EXPECT_SOURCE ?? 'Template'

function demoPassword() {
  if (process.env.DEMO_PASSWORD) return process.env.DEMO_PASSWORD
  try {
    const line = fs.readFileSync('../.env', 'utf8').split(/\r?\n/).find((l) => l.startsWith('DEMO_PASSWORD='))
    return line ? line.slice('DEMO_PASSWORD='.length).trim() : ''
  } catch {
    return ''
  }
}
const PASSWORD = demoPassword()
if (!PASSWORD) {
  console.error('DEMO_PASSWORD is not set (environment or ../.env); cannot sign in.')
  process.exit(2)
}

fs.mkdirSync(SHOTS, { recursive: true })
const results = []
const problems = []

function check(name, ok, detail = '') {
  results.push({ name, ok })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`)
}

const browser = await chromium.launch({ executablePath: CHROME, headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true })
const page = await context.newPage()

page.on('console', (m) => {
  if (['error', 'warning'].includes(m.type())) problems.push(`console.${m.type()}: ${m.text()}`)
})
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
page.on('requestfailed', (r) => {
  if (r.failure()?.errorText !== 'net::ERR_ABORTED') problems.push(`request failed: ${r.url()} ${r.failure()?.errorText}`)
})
page.on('response', (r) => {
  if (r.status() >= 400) problems.push(`HTTP ${r.status()}: ${r.url()}`)
})

const shot = (name) => page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })

async function noBannedWords(label) {
  const text = (await page.evaluate(() => document.body.innerText)).toLowerCase()
  const hit = BANNED.filter((w) => text.includes(w))
  check(`no banned wording on ${label}`, hit.length === 0, hit.join(', '))
}

async function signIn(username, password = PASSWORD) {
  await page.goto(`${BASE}/login`)
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}
async function signedInAs() {
  await page.waitForSelector('[data-testid=current-user]')
  return (await page.textContent('[data-testid=current-user]')).replace(/\s+/g, ' ').trim()
}
async function signOut() {
  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.waitForSelector('#username')
}
const navLinks = async () =>
  (await page.getByRole('navigation', { name: 'Main' }).getByRole('link').allInnerTexts()).map((t) => t.trim())

/** Click a download button and return the saved file's name and first bytes. */
async function download(buttonName) {
  const [file] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: buttonName }).click()])
  const target = `${SHOTS}/${file.suggestedFilename()}`
  await file.saveAs(target)
  const bytes = fs.readFileSync(target)
  return { name: file.suggestedFilename(), size: bytes.length, head: bytes.subarray(0, 5).toString('latin1'), text: bytes.toString('utf8') }
}

async function api(path, token, options = {}) {
  const response = await fetch(`${API}${path}`, { ...options, headers: { ...(options.headers ?? {}), Authorization: `Bearer ${token}` } })
  return response.json()
}
async function apiLogin(username) {
  const response = await fetch(`${API}/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password: PASSWORD }),
  })
  return (await response.json()).access_token
}

let assignedCase = null

try {
  // ---------------------------------------------------------------- signing in
  await page.goto(`${BASE}/queue`)
  await page.waitForSelector('#username')
  check('someone who is not signed in is sent to the login page', page.url().endsWith('/login'))
  await signIn('south_lead', 'definitely-not-the-password')
  await page.waitForSelector('[data-testid=login-error]')
  check('a wrong password shows the API message and keeps the field empty', /Wrong username or password/.test(await page.textContent('[data-testid=login-error]')) && (await page.getByLabel('Password').inputValue()) === '')
  check('no token is stored after a wrong password', (await page.evaluate(() => window.sessionStorage.getItem('claimshield.token'))) === null)

  // ---------------------------------------------------------------- admin
  await signIn('admin')
  await page.waitForSelector('[data-testid=amount-at-risk]')
  check('the admin sees the admin top bar', (await signedInAs()).startsWith('System Admin · Admin'), await signedInAs())
  check('admin navigation is Overview, All Cases, System', JSON.stringify(await navLinks()) === JSON.stringify(['Overview', 'All Cases', 'System']), (await navLinks()).join(' | '))
  const amount = await page.textContent('[data-testid=amount-at-risk]')
  check('overview shows the amount at risk', /^Rs [\d,]+$/.test(amount.trim()), amount.trim())
  check('overview counts all 20 cases', /20 awaiting review/.test(await page.textContent('[data-testid=tile-cases]')))
  await noBannedWords('Overview')
  await shot('1-admin-overview')
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'All Cases' }).click()
  await page.waitForSelector('[data-testid=queue-row]')
  check('the admin sees every case', (await page.locator('[data-testid=queue-row]').count()) === 20)
  await page.locator('[data-testid=queue-row] a').first().click()
  await page.waitForSelector('[data-testid=assignment-panel]')
  check('the admin can look at a case but not decide on it', (await page.getByTestId('read-only-note').isVisible()) && (await page.getByTestId('decision-panel').count()) === 0)
  check('the admin cannot draft messages', (await page.getByTestId('outbound-panel').count()) === 0)
  const adminPdf = await download('Download case report (PDF)')
  check('the admin can download a case report', adminPdf.head === '%PDF-' && /^CASE-\d{4}-report\.pdf$/.test(adminPdf.name) && adminPdf.size > 2000, `${adminPdf.name} ${adminPdf.size} bytes`)
  await noBannedWords('Case detail (admin)')
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'System' }).click()
  await page.waitForSelector('[data-testid=users-table]')
  check('System lists the 7 seeded users and 2 units', (await page.locator('[data-testid=users-table] tbody tr').count()) === 7 && (await page.locator('[data-testid=units-table] tbody tr').count()) === 2)
  check('no case is unrouted', /None/.test(await page.textContent('[data-testid=unrouted]')))
  await page.getByRole('button', { name: 'Send test email' }).click()
  await page.waitForSelector('[data-testid=task-result]')
  check('"Send test email" shows a result', /Success\.|Not sent\./.test(await page.textContent('[data-testid=task-result]')), (await page.textContent('[data-testid=task-result]')).trim())
  await shot('2-admin-system')
  await signOut()
  check('signing out ends the session', (await page.evaluate(() => window.sessionStorage.getItem('claimshield.token'))) === null)

  // ---------------------------------------------------------------- team lead
  await signIn('south_lead')
  await page.waitForSelector('[data-testid=section-unassigned]')
  check('the team lead sees the lead top bar', /^Kavya Menon · Team lead · Unit South/.test(await signedInAs()), await signedInAs())
  check('team lead navigation is Unit Queue, Team Workload', JSON.stringify(await navLinks()) === JSON.stringify(['Unit Queue', 'Team Workload']), (await navLinks()).join(' | '))
  const unitRows = await page.locator('[data-testid=queue-row]').count()
  check('the lead sees only their unit’s cases', unitRows > 0 && unitRows < 20, `${unitRows} of 20`)
  const unassigned = page.locator('section[aria-label=Unassigned] [data-testid=queue-row]')
  assignedCase = await unassigned.first().getAttribute('data-case')
  await unassigned.first().getByRole('button', { name: /^Assign CASE/ }).click()
  await page.getByTestId('assign-form').getByRole('button', { name: 'Confirm' }).click()
  check('assigning without a reason is blocked', /A reason is required/.test(await page.textContent('[data-testid=assign-error]')))
  await page.getByTestId('assign-form').getByLabel('Assign to').selectOption({ label: 'Arjun Nair' })
  await page.getByTestId('assign-form').getByLabel('Reason (required)').fill('Arjun has the capacity this week')
  await page.getByTestId('assign-form').getByRole('button', { name: 'Confirm' }).click()
  await page.waitForFunction((id) => !!document.querySelector(`section[aria-label=Assigned] [data-case="${id}"]`), assignedCase)
  check('the assigned case moves to the Assigned section with the investigator’s name', /Arjun Nair/.test(await page.locator(`section[aria-label=Assigned] [data-case="${assignedCase}"] [data-testid=assignee-cell]`).textContent()), assignedCase)
  await shot('3-lead-unit-queue')
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Team Workload' }).click()
  await page.waitForSelector('[data-testid=workload-card]')
  const arjun = page.locator('[data-testid=workload-card]', { hasText: 'Arjun Nair' })
  check('Team Workload shows the new case and an effort bar', /1 open/.test(await arjun.textContent()) && (await arjun.getByRole('meter').count()) === 1)
  const unitPdf = await download('Download unit report (PDF)')
  check('the unit report PDF downloads', unitPdf.head === '%PDF-' && /report\.pdf$/.test(unitPdf.name), `${unitPdf.name} ${unitPdf.size} bytes`)
  const unitCsv = await download('Download unit report (CSV)')
  check('the unit report CSV downloads with the expected columns', unitCsv.text.split('\n')[0].trim() === 'case_id,title,rank,priority,priority_band,status,assignee,latest_decision,decision_date,decided_by,amount_at_risk_rs,pending_days,overdue' && unitCsv.text.includes(assignedCase), unitCsv.name)
  await shot('4-lead-workload')
  await page.goto(`${BASE}/cases/${assignedCase}`)
  await page.waitForSelector('[data-testid=assignment-panel]')
  check('the lead sees the assignment, a decision panel and the report button', /with Arjun Nair/.test(await page.textContent('[data-testid=assignment-summary]')) && (await page.getByTestId('decision-panel').isVisible()) && (await page.getByRole('button', { name: 'Download case report (PDF)' }).isVisible()))
  const leadToken = await apiLogin('south_lead')
  const adminToken = await apiLogin('admin')
  const everything = (await api('/queue?capacity=1000&include_decided=true', adminToken))
  const allIds = [...everything.scheduled, ...everything.backlog].map((i) => i.case_id)
  const mine = (await api('/queue?capacity=1000&include_decided=true', leadToken))
  const myIds = new Set([...mine.scheduled, ...mine.backlog].map((i) => i.case_id))
  const otherUnitCase = allIds.find((id) => !myIds.has(id))
  await page.goto(`${BASE}/cases/${otherUnitCase}`)
  await page.waitForSelector('[role=alert]')
  check('another unit’s case is refused with a clear message', /do not have access/i.test(await page.textContent('[role=alert]')), otherUnitCase)
  await noBannedWords('the access-refused page')
  await page.goto(`${BASE}/system`)
  await page.waitForSelector('[data-testid=top-bar]')
  check('a team lead who types /system is sent back to their queue', page.url().endsWith('/unit-queue'))
  await signOut()

  // ---------------------------------------------------------------- investigator
  await signIn('south_inv1')
  await page.waitForSelector('[data-testid=current-user]')
  check('the investigator lands on My Cases', (await page.textContent('h1')).trim() === 'My Cases', page.url())
  check('investigator navigation is My Cases, Unit Queue', JSON.stringify(await navLinks()) === JSON.stringify(['My Cases', 'Unit Queue']), (await navLinks()).join(' | '))
  await page.waitForSelector('[data-testid=queue-row]')
  const mineRows = await page.locator('[data-testid=queue-row]').evaluateAll((rows) => rows.map((r) => r.dataset.case))
  check('My Cases lists only the case assigned to them', mineRows.length === 1 && mineRows[0] === assignedCase, mineRows.join(','))
  await page.getByTestId('bell').click()
  await page.waitForSelector('[data-testid=notification-panel]')
  check('the assignment notification is in their bell', new RegExp(`Case ${assignedCase} assigned to you by Kavya Menon`).test(await page.textContent('[data-testid=notification-panel]')))
  await shot('5-investigator-bell')
  await page.keyboard.press('Escape')
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Unit Queue' }).click()
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=queue-row]').length > 1)
  const linked = await page.locator('[data-testid=queue-row]').evaluateAll((rows) => rows.filter((r) => r.querySelector('a')).map((r) => r.dataset.case))
  check('in the read-only Unit Queue only their own case is a link', linked.length === 1 && linked[0] === assignedCase, linked.join(','))
  check('the Unit Queue has no assignment controls for an investigator', (await page.getByRole('button', { name: /Assign|Reassign/ }).count()) === 0)
  const unassignedCase = (await page.locator('[data-testid=queue-row]').evaluateAll((rows) => rows.filter((r) => !r.querySelector('a')).map((r) => r.dataset.case)))[0]
  await page.goto(`${BASE}/cases/${unassignedCase}`)
  await page.waitForSelector('[role=alert]')
  check('a case that is not theirs is refused, even in their own unit', /do not have access/i.test(await page.textContent('[role=alert]')), unassignedCase)
  await page.goto(`${BASE}/cases/${assignedCase}`)
  await page.waitForSelector('[data-testid=decision-panel]')
  check('their own case opens with the decision panel', true)
  check('there is no name field: the decision is recorded under their sign-in', (await page.getByLabel('Your name').count()) === 0)
  await page.getByRole('button', { name: 'Open investigation' }).click()
  check('a decision without a reason is blocked', /A reason is required/.test(await page.textContent('[data-testid=decision-error]')))
  await page.getByLabel('Reason (required)').fill('Referral pattern and shared ownership need verification')
  await page.getByRole('button', { name: 'Open investigation' }).click()
  await page.waitForSelector('[data-testid=decision-confirmation]')
  check('the decision is confirmed with their name', /by Arjun Nair/.test(await page.textContent('[data-testid=decision-confirmation]')))
  await page.waitForFunction(() => /Arjun Nair/.test(document.querySelector('[data-testid=decision-history]')?.textContent ?? ''))
  check('the history shows who decided', true)
  check('the assignment status becomes In review', /In review/.test(await page.textContent('[data-testid=assignment-panel]')))
  const provider = page.locator('select[aria-label="Request records recipient"]')
  await page.getByRole('button', { name: 'Request records', exact: true }).click()
  await page.waitForSelector('[data-testid=outbound-draft]')
  check('a draft is shown with the notice and the approver is the signed-in user', /will not be sent until you approve it/.test(await page.getByRole('note').textContent()) && /You approve as Arjun Nair/.test(await page.textContent('[data-testid=approver]')) && (await provider.count()) === 1)
  await page.getByLabel(/Reason for approving/).fill('Wording checked')
  await page.getByRole('button', { name: /Approve & send/ }).click()
  await page.getByTestId('outbound-history').getByText('Sent (simulated)').waitFor()
  check('the message is only ever marked sent (simulated)', true)
  const caseReport = await download('Download case report (PDF)')
  check('they can download their own case report', caseReport.head === '%PDF-' && caseReport.name === `${assignedCase}-report.pdf`, `${caseReport.name} ${caseReport.size} bytes`)
  const brief = await page.textContent('[data-testid=brief-source]')
  check(`the brief badge reads "${EXPECT_SOURCE}"`, brief.trim() === EXPECT_SOURCE || brief.trim() === `${EXPECT_SOURCE} (cached)`, brief.trim())
  await noBannedWords('Case detail (investigator)')
  await shot('6-investigator-case')
  await page.goto(`${BASE}/system`)
  await page.waitForSelector('[data-testid=current-user]')
  check('an investigator who types /system is sent to My Cases', (await page.textContent('h1')).trim() === 'My Cases')
  await signOut()

  // ---------------------------------------------------------------- the API agrees, and the log shows it
  const inv = await apiLogin('south_inv1')
  const denied = await fetch(`${API}/cases/${otherUnitCase}`, { headers: { Authorization: `Bearer ${inv}` } })
  check('the API itself refuses the investigator another unit’s case (403)', denied.status === 403)
  const noToken = await fetch(`${API}/queue`)
  check('the API refuses a request with no token (401)', noToken.status === 401)
  const refusedAssign = await fetch(`${API}/cases/${assignedCase}/assign`, {
    method: 'POST', headers: { Authorization: `Bearer ${inv}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ assignee_user_id: 4, reason: 'Trying it anyway' }),
  })
  check('the API refuses an investigator assigning a case (403)', refusedAssign.status === 403)
  const audit = await api('/audit?limit=1000', adminToken)
  const kinds = (type) => audit.filter((e) => e.event_type === type)
  check('the audit log has the logins', kinds('login').length >= 4 && kinds('login_failed').length >= 1)
  check('the audit log has the assignment', kinds('assignment').some((e) => e.case_id === assignedCase && e.reviewer === 'Kavya Menon' && e.reason === 'Arjun has the capacity this week'))
  check('the audit log has the decision under the investigator’s name', kinds('decision').some((e) => e.case_id === assignedCase && e.reviewer === 'Arjun Nair'))
  const downloads = kinds('report_download').map((e) => e.action).sort()
  check('every report download is audited', ['case_pdf', 'case_pdf', 'unit_csv', 'unit_pdf'].every((a, i) => downloads[i] === a), downloads.join(','))
  check('refused attempts are audited', kinds('access_denied').length >= 3, `${kinds('access_denied').length} entries`)
  check('outbound create and approval are audited', kinds('outbound_create').length === 1 && kinds('outbound_approve').length === 1)
} catch (error) {
  check('the flow ran to the end', false, error.message.split('\n')[0])
  await shot('failure')
}

// The refusals above are the point of the test: 401 for the wrong password, 403 for access that
// is not allowed, and 422 never. Anything else is a problem.
const expected = (p) => /HTTP 401: .*\/auth\/login/.test(p) || /HTTP 403/.test(p) || /status of 40[13]/.test(p)
const unexpected = problems.filter((p) => !expected(p))
check('browser console has no unexpected errors or warnings', unexpected.length === 0, unexpected.slice(0, 3).join(' | '))
await browser.close()

const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length} of ${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
