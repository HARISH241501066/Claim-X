// End-to-end check in a real Chrome: Overview -> Queue -> top case -> brief -> decision.
// It expects the API (ideally with CLAIMSHIELD_AUDIT_PATH set to a throwaway file) and the Vite
// dev server to be running. It fails if the browser console shows any error or warning.
import fs from 'node:fs'
import { chromium } from 'playwright-core'

const BASE = process.env.BASE_URL ?? 'http://localhost:5173'
const CHROME = process.env.CHROME_PATH ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const SHOTS = process.env.SHOT_DIR ?? 'e2e/screenshots'
const BANNED = ['fraud' + ' probability', 'chance of ' + 'fraud', 'fraud' + '_prob']
const TOOLTIP =
  'Estimated likelihood of a confirmed investigation within 30 days, based on synthetic history. Not a finding of fraud.'

fs.mkdirSync(SHOTS, { recursive: true })
const results = []
const problems = []
const posts = []

function check(name, ok, detail = '') {
  results.push({ name, ok })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`)
}

const browser = await chromium.launch({ executablePath: CHROME, headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

page.on('console', (m) => {
  if (['error', 'warning'].includes(m.type())) problems.push(`console.${m.type()}: ${m.text()}`)
})
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
page.on('requestfailed', (r) => {
  // The app cancels its own requests when a screen changes (and React's dev mode does so once
  // at start-up); a cancellation is not a failure, anything else is.
  if (r.failure()?.errorText !== 'net::ERR_ABORTED') problems.push(`request failed: ${r.url()} ${r.failure()?.errorText}`)
})
page.on('response', (r) => {
  if (r.status() >= 400) problems.push(`HTTP ${r.status()}: ${r.url()}`)
})
page.on('request', (r) => {
  if (r.method() === 'POST') posts.push(r.url())
})

async function noBannedWords(label) {
  const text = (await page.evaluate(() => document.body.innerText)).toLowerCase()
  const html = (await page.content()).toLowerCase()
  const hit = BANNED.filter((w) => text.includes(w) || html.includes(w))
  check(`no banned wording on ${label}`, hit.length === 0, hit.join(', '))
}
const shot = (name) => page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })

try {
  // ---- Overview
  await page.goto(BASE)
  await page.waitForSelector('[data-testid=amount-at-risk]')
  const amount = await page.textContent('[data-testid=amount-at-risk]')
  check('overview shows the amount at risk', /^Rs [\d,]+$/.test(amount.trim()), amount.trim())
  const tiles = {}
  for (const t of ['claims', 'findings', 'cases', 'scheduled']) {
    tiles[t] = await page.textContent(`[data-testid=tile-${t}] p:nth-of-type(2)`)
  }
  check('funnel tiles claims > findings > cases > scheduled', Number(tiles.claims.replace(/,/g, '')) > Number(tiles.findings) && Number(tiles.findings) > Number(tiles.cases) && Number(tiles.cases) > Number(tiles.scheduled), JSON.stringify(tiles))
  await page.waitForSelector('.recharts-bar-rectangle')
  const bars = await page.locator('.recharts-bar-rectangle').count()
  check('findings-per-rule bar chart draws one bar per detector', bars >= 8, `${bars} bars`)
  await page.getByRole('button', { name: 'View as table' }).click()
  check('chart has a table view', (await page.locator('table tbody tr').count()) >= 8)
  await page.getByRole('button', { name: 'View as chart' }).click()
  await noBannedWords('Overview')
  await shot('1-overview')

  // ---- Queue
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Queue' }).click()
  await page.waitForSelector('[data-testid=queue-row]')
  const rowCount = await page.locator('[data-testid=queue-row]').count()
  check('queue lists every case', rowCount === 20, `${rowCount} rows`)
  check('scheduled and backlog dividers are shown', (await page.locator('[data-testid=divider-scheduled]').count()) === 1 && (await page.locator('[data-testid=divider-backlog]').count()) === 1)
  const firstRow = page.locator('[data-testid=queue-row]').first()
  const firstText = await firstRow.innerText()
  check('top case is the ring and shows a Ring badge', /Referral network/.test(firstText) && /\bRing\b/.test(firstText), firstText.split('\n')[0])
  check('top row shows detector chips Rules · ML · Graph', /Rules/.test(firstText) && /ML/.test(firstText) && /Graph/.test(firstText))
  check('top row shows an amount', /Rs [\d,]+/.test(firstText))
  const summaryBefore = await page.textContent('[data-testid=queue-summary]')
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/queue') && r.url().includes('capacity=20')),
    page.locator('#capacity').fill('20'),
  ])
  await page.waitForFunction((before) => document.querySelector('[data-testid=queue-summary]')?.textContent !== before, summaryBefore)
  const summaryAfter = await page.textContent('[data-testid=queue-summary]')
  check('capacity slider re-schedules the queue', /of 20 h/.test(summaryAfter), summaryAfter.trim())
  await page.locator('#capacity').fill('40')
  await page.getByRole('button', { name: 'Priority weights' }).click()
  const pctOf = async (k) => Number((await page.textContent(`[data-testid=weight-${k}]`)).replace('%', ''))
  const parts = await Promise.all(['risk', 'dollars', 'impact', 'severity', 'evidence'].map(pctOf))
  check('weight shares add up to 100%', parts.reduce((a, b) => a + b, 0) === 100, parts.join('+'))
  await page.getByLabel('Risk weight').fill('60')
  const parts2 = await Promise.all(['risk', 'dollars', 'impact', 'severity', 'evidence'].map(pctOf))
  check('moving one slider keeps the shares at 100%', parts2.reduce((a, b) => a + b, 0) === 100 && parts2[0] > parts[0], parts2.join('+'))
  await page.getByRole('button', { name: 'Reset to defaults' }).click()
  await noBannedWords('Queue')
  await shot('2-queue')

  // ---- Case detail (top case)
  await page.locator('[data-testid=queue-row] a').first().click()
  await page.waitForSelector('h1')
  await page.waitForSelector('[data-testid=evidence-item]')
  check('case title is readable', /Referral network/.test(await page.textContent('h1')))
  check('header shows priority, "X of 3 detectors" and a confidence badge',
    /Priority\s*0\.\d+/.test(await page.textContent('[data-testid=header-priority]')) &&
    /\d of 3 detectors/.test(await page.textContent('[data-testid=header-detectors]')) &&
    /Confidence: (High|Medium|Low)/.test(await page.textContent('[data-testid=confidence-badge]')))
  check('evidence list shows E1 and E2 with IDs', (await page.locator('[data-testid=evidence-item]').count()) === 2 && /CLM-\d+|PRV-/.test(await page.textContent('#evidence-E2')))
  await page.locator('#evidence-E2').getByRole('button', { name: 'Show claim rows' }).click()
  await page.waitForSelector('[data-testid=rows-E2] [data-testid=claim-row]')
  const claimRows = await page.locator('[data-testid=rows-E2] [data-testid=claim-row]').count()
  check('clicking evidence shows its claim rows', claimRows === 180, `${claimRows} rows`)
  await page.waitForSelector('[data-testid=network-graph] canvas')
  check('network graph draws on a canvas', (await page.locator('[data-testid=network-graph] canvas').count()) >= 1)
  check('graph has a legend and a table view', (await page.getByRole('list', { name: 'Legend' }).count()) === 1 && (await page.locator('summary', { hasText: 'Table view' }).count()) === 1)
  check('timeline lists dated events', (await page.locator('[data-testid=timeline-entry]').count()) >= 3)
  const riskTitle = await page.locator('[data-testid=risk-panel] h2').textContent()
  check('risk panel is titled "30-Day Investigation Risk: X%"', /^30-Day Investigation Risk: \d+(\.\d)?%$/.test(riskTitle.trim()), riskTitle.trim())
  await page.getByRole('button', { name: 'About this estimate' }).hover()
  const tip = page.getByRole('tooltip')
  check('risk tooltip has the exact caveat and is visible on hover', (await tip.isVisible()) && (await tip.textContent()).trim() === TOOLTIP)
  check('60 and 90 day windows are disabled (not trained)', (await page.getByRole('radio', { name: '60 days' }).isDisabled()) && (await page.getByRole('radio', { name: '90 days' }).isDisabled()) && !(await page.getByRole('radio', { name: '30 days' }).isDisabled()))
  check('risk panel shows a band and drivers', (await page.locator('[data-testid=risk-panel]').innerText()).match(/Low|Medium|High/) !== null && /Top drivers/.test(await page.locator('[data-testid=risk-panel]').innerText()))
  await noBannedWords('Case detail (before decision)')

  // ---- Brief
  await page.waitForSelector('[data-testid=brief-body]')
  const sourceText = await page.textContent('[data-testid=brief-source]')
  check('brief states its source (LLM or template)', /Source: (LLM|template)/.test(sourceText), sourceText.trim())
  const cites = await page.locator('[data-testid=brief-body] [data-cite]').count()
  check('brief citations are clickable', cites >= 2, `${cites} citations`)
  await page.locator('[data-testid=brief-body] [data-cite=E1]').first().click()
  check('clicking a citation selects its evidence', (await page.locator('#evidence-E1').getAttribute('aria-current')) === 'true')
  await shot('3-case-top')

  // ---- Human review
  check('"AI Recommendation" and "Your Decision" are separate panels', (await page.getByRole('heading', { name: 'AI Recommendation' }).isVisible()) && (await page.getByRole('heading', { name: 'Your Decision' }).isVisible()))
  for (const label of ['Open investigation', 'Request records', 'Dismiss']) {
    check(`button "${label}" is present`, await page.getByRole('button', { name: label }).isVisible())
  }
  const postsBefore = posts.length
  await page.getByRole('button', { name: 'Open investigation' }).click()
  check('no name and no reason: blocked with a message', (await page.getByTestId('decision-error').isVisible()))
  await page.getByLabel('Your name').fill('Asha Rao')
  await page.getByRole('button', { name: 'Dismiss' }).click()
  check('a name but no reason: still blocked', /reason is required/i.test(await page.getByTestId('decision-error').textContent()))
  await page.getByLabel('Reason (required)').fill('hmm')
  await page.getByRole('button', { name: 'Request records' }).click()
  check('a too-short reason is blocked', /at least 5/.test(await page.getByTestId('decision-error').textContent()))
  check('blocked attempts sent nothing to the server', posts.length === postsBefore)
  await page.getByLabel('Reason (required)').fill('Referral pattern and shared ownership need verification')
  await page.getByRole('button', { name: 'Open investigation' }).click()
  await page.waitForSelector('[data-testid=decision-confirmation]')
  check('a decision with a reason is recorded', /Escalated for investigation/.test(await page.textContent('[data-testid=decision-confirmation]')))
  await page.waitForFunction(() => /Escalated for investigation/.test(document.querySelector('[data-testid=status-badge]')?.textContent ?? ''))
  check('case status changes', true)
  check('the decision appears in the history with the reviewer', /Asha Rao/.test(await page.textContent('[data-testid=decision-history]')))
  await shot('4-after-decision')
  await page.reload()
  await page.waitForSelector('[data-testid=status-badge]')
  check('the decision survives a reload', /Escalated for investigation/.test(await page.textContent('[data-testid=status-badge]')))
  await noBannedWords('Case detail (after decision)')

  // ---- Back to the queue
  await page.getByRole('link', { name: '← Back to the queue' }).click()
  await page.waitForSelector('[data-testid=queue-row]')
  const nextTop = await page.locator('[data-testid=queue-row]').first().innerText()
  check('decided case leaves the queue', !/Referral network/.test(nextTop) && (await page.locator('[data-testid=queue-row]').count()) === 19, nextTop.split('\n')[0])
  await page.getByLabel('Show decided cases').check()
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=queue-row]').length === 20)
  check('"Show decided cases" brings it back with its status', /Escalated for investigation/.test(await page.locator('[data-testid=queue-row]').first().innerText()))
  await page.getByLabel('Show decided cases').uncheck()
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=queue-row]').length === 19)

  // ---- Priority override on another case
  const target = page.locator('[data-testid=queue-row]').nth(3)
  const targetId = await target.getAttribute('data-case')
  await target.locator('a').click()
  await page.waitForSelector('[data-testid=decision-panel]')
  await page.getByLabel('Your priority').fill('99')
  await page.getByRole('button', { name: 'Set priority' }).click()
  check('an override without a reason is blocked', (await page.getByTestId('override-error').isVisible()))
  await page.getByLabel('Reason for the override (required)').fill('Provider records arrive this week')
  await page.getByRole('button', { name: 'Set priority' }).click()
  await page.waitForSelector('[data-testid=override-confirmation]')
  await page.getByRole('link', { name: '← Back to the queue' }).click()
  await page.waitForSelector('[data-testid=queue-row]')
  const pinned = page.locator('[data-testid=queue-row]').first()
  check('the overridden case moves to the top and shows AI vs your priority', (await pinned.getAttribute('data-case')) === targetId && /AI 0\.\d+ → Asha Rao 0\.99/.test(await pinned.innerText()))
  await shot('5-queue-after-override')

  // ---- Overview reflects the decision
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Overview' }).click()
  await page.waitForSelector('[data-testid=tile-cases]')
  check('overview counts the decided case', /1 decided/.test(await page.textContent('[data-testid=tile-cases]')))

  // ---- A page that does not exist still renders something
  await page.goto(`${BASE}/cases/CASE-9999`)
  await page.waitForSelector('[role=alert]')
  check('an unknown case shows an error with retry, not a blank page', /Unknown case/.test(await page.textContent('[role=alert]')) && (await page.getByRole('button', { name: 'Try again' }).count()) >= 1)
  await page.goto(`${BASE}/nowhere`)
  check('an unknown address shows a not-found page', await page.getByRole('heading', { name: 'Page not found' }).isVisible())
} catch (error) {
  check('the flow ran to the end', false, error.message.split('\n')[0])
  await shot('failure')
}

// Unknown case => the API answers 404 on purpose; everything else must be clean.
const unexpected = problems.filter((p) => !/CASE-9999/.test(p) && !/status of 404/.test(p))
check('browser console has no errors or warnings', unexpected.length === 0, unexpected.slice(0, 3).join(' | '))
await browser.close()

const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length} of ${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
