import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { briefData, caseDetail, evidenceRows, factors, prediction, providerItem, queueData, queueItem } from '../test/fixtures'
import Async from './Async'
import Brief from './Brief'
import DetectorChips from './DetectorChips'
import FindingsChart from './FindingsChart'
import QueueTable from './QueueTable'
import { FactorBars, PriorityBar } from './PriorityBar'
import ReviewPanel from './ReviewPanel'
import RiskPanel from './RiskPanel'
import Timeline from './Timeline'
import WeightControls from './WeightControls'
import { DEFAULT_WEIGHTS } from '../lib/weights'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal()),
  postDecision: vi.fn(),
  postOverride: vi.fn(),
  getEvidence: vi.fn(),
}))

const BANNED = ['fraud' + ' probability', 'chance of ' + 'fraud']
const lower = () => document.body.textContent.toLowerCase()

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
})

describe('Async', () => {
  it('shows a skeleton while loading, never a blank', () => {
    render(<Async state={{ data: null, error: null, loading: true, reload: vi.fn() }}>{() => <p>content</p>}</Async>)
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByText('content')).not.toBeInTheDocument()
  })

  it('shows the error with a working Try again button', async () => {
    const reload = vi.fn()
    render(<Async state={{ data: null, error: 'Cannot reach the API', loading: false, reload }}>{() => 'x'}</Async>)
    expect(screen.getByRole('alert')).toHaveTextContent('Cannot reach the API')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('keeps showing old data, with a notice, when a refresh fails', () => {
    render(<Async state={{ data: 'old', error: 'boom', loading: false, reload: vi.fn() }}>{(d) => <p>{d} data</p>}</Async>)
    expect(screen.getByText('old data')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Could not refresh: boom')
  })

  it('renders the content when data is ready', () => {
    render(<Async state={{ data: 5, error: null, loading: false, reload: vi.fn() }}>{(d) => <p>value {d}</p>}</Async>)
    expect(screen.getByText('value 5')).toBeInTheDocument()
  })
})

describe('Brief', () => {
  const keys = new Set(['E1', 'E2'])

  it('turns known citations into clickable chips and leaves unknown ones as plain text', async () => {
    const onCite = vi.fn()
    render(<Brief brief={briefData()} validKeys={keys} onCite={onCite} />)
    const body = screen.getByTestId('brief-body')
    const chips = body.querySelectorAll('[data-cite]')
    expect([...chips].map((c) => c.dataset.cite)).toEqual(['E1', 'E2', 'E1', 'E2'])
    expect(body).toHaveTextContent('[E9]') // shown, but not a link
    expect(body.querySelector('[data-cite=E9]')).toBeNull()
    await userEvent.click(chips[1])
    expect(onCite).toHaveBeenCalledWith('E2')
  })

  it('renders headings and bullets', () => {
    render(<Brief brief={briefData()} validKeys={keys} onCite={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'Summary' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Evidence' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByText(/Final decision rests with the assigned investigator/)).toBeInTheDocument()
  })

  it('shows the Template badge, with the reason, and no masked-data note', () => {
    render(<Brief brief={briefData({ fallback_reason: 'LLM_PROVIDER is none' })} validKeys={keys} onCite={vi.fn()} />)
    expect(screen.getByTestId('brief-source')).toHaveTextContent(/^Template$/)
    expect(screen.getByText(/LLM_PROVIDER is none/)).toBeInTheDocument()
    expect(screen.queryByTestId('masked-note')).toBeNull()
  })

  it.each([
    ['anthropic', 'Claude', 'claude-opus-5-5'],
    ['xai', 'Grok', 'grok-4'],
    ['groq', 'Groq', 'openai/gpt-oss-120b'],
  ])('names the real provider on an LLM brief: %s is shown as %s', (provider, label, model) => {
    render(
      <Brief
        brief={briefData({ source: 'llm', provider, provider_label: label, model, masked: true, fallback_reason: null })}
        validKeys={keys}
        onCite={vi.fn()}
      />,
    )
    const badge = screen.getByTestId('brief-source')
    expect(badge).toHaveTextContent(new RegExp(`^${label}$`))
    expect(badge).toHaveAttribute('title', `Model: ${model}`)
    expect(screen.getByTestId('masked-note')).toHaveTextContent(
      'Generated from masked data. No personal details were shared.',
    )
    expect(screen.getByText(/Checked against the evidence/)).toBeInTheDocument()
  })
})

describe('Brief badge', () => {
  const show = (over) =>
    render(<Brief brief={briefData({ source: 'llm', provider: 'groq', provider_label: 'Groq', model: 'm', masked: true, fallback_reason: null, ...over })} validKeys={new Set()} onCite={vi.fn()} />)

  it('says "Groq (cached)" when stored text was reused and "Groq" when freshly written', () => {
    const { unmount } = show({ cached: true })
    expect(screen.getByTestId('brief-source')).toHaveTextContent(/^Groq \(cached\)$/)
    unmount()
    show({ cached: false })
    expect(screen.getByTestId('brief-source')).toHaveTextContent(/^Groq$/)
  })

  it('never adds "(cached)" to a template brief', () => {
    render(<Brief brief={briefData({ cached: true })} validKeys={new Set()} onCite={vi.fn()} />)
    expect(screen.getByTestId('brief-source')).toHaveTextContent(/^Template$/)
  })
})

describe('Brief formatting from an LLM', () => {
  const keys = new Set(['E1', 'E2'])
  const llmBrief = (text) => briefData({ source: 'llm', provider: 'groq', provider_label: 'Groq', model: 'm', masked: true, fallback_reason: null, brief: text })

  it('renders bold text and keeps citations clickable inside it', async () => {
    const onCite = vi.fn()
    render(<Brief brief={llmBrief('Provider **PRV-A01 [E1]** is flagged.')} validKeys={keys} onCite={onCite} />)
    const bold = document.querySelector('strong')
    expect(bold).toHaveTextContent('PRV-A01 [E1]')
    expect(document.body.textContent).not.toContain('**')
    await userEvent.click(within(bold).getByRole('button', { name: '[E1]' }))
    expect(onCite).toHaveBeenCalledWith('E1')
  })

  it('renders a markdown table as a table, with citations in its cells clickable', async () => {
    const onCite = vi.fn()
    const text = ['| Date | Claims | Evidence |', '|------|--------|----------|', '| 2026-01-02 | 30 | [E2] |'].join('\n')
    render(<Brief brief={llmBrief(text)} validKeys={keys} onCite={onCite} />)
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Date', 'Claims', 'Evidence'])
    expect(screen.getAllByRole('cell').map((c) => c.textContent)).toEqual(['2026-01-02', '30', '[E2]'])
    expect(document.body.textContent).not.toContain('|---')
    await userEvent.click(screen.getByRole('button', { name: '[E2]' }))
    expect(onCite).toHaveBeenCalledWith('E2')
  })

  it('treats a table right after a list, and a list with * markers, correctly', () => {
    render(<Brief brief={llmBrief('* first [E1]\n* second\n| A | B |\n|---|---|\n| 1 | 2 |')} validKeys={keys} onCite={vi.fn()} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getAllByRole('cell')).toHaveLength(2)
  })
})

describe('RiskPanel', () => {
  it('is titled with the horizon and the percentage', () => {
    render(<RiskPanel prediction={prediction()} />)
    expect(screen.getByRole('heading', { name: '30-Day Investigation Risk: 2.8%' })).toBeInTheDocument()
  })

  it('carries the exact caveat in its tooltip, and never the banned wording', () => {
    render(<RiskPanel prediction={prediction()} />)
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      'Estimated likelihood of a confirmed investigation within 30 days, based on synthetic history. Not a finding of fraud.',
    )
    BANNED.forEach((phrase) => expect(lower()).not.toContain(phrase))
  })

  it('offers 30, 60 and 90 days but only the trained 30-day window is selectable', async () => {
    const onHorizon = vi.fn()
    render(<RiskPanel prediction={prediction()} onHorizon={onHorizon} />)
    expect(screen.getByRole('radio', { name: '30 days' })).toBeEnabled()
    expect(screen.getByRole('radio', { name: '60 days' })).toBeDisabled()
    expect(screen.getByRole('radio', { name: '90 days' })).toBeDisabled()
    await userEvent.click(screen.getByRole('radio', { name: '90 days' }))
    expect(onHorizon).not.toHaveBeenCalled()
  })

  it('shows the band, the top three drivers and a low-confidence notice', () => {
    const drivers = ['a 1', 'b 2', 'c 3', 'd 4']
    render(<RiskPanel prediction={prediction({ risk_band: 'Medium', top_drivers: drivers, low_confidence: true })} />)
    expect(screen.getByText('Medium')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual(['a 1', 'b 2', 'c 3'])
    expect(screen.getByText(/Low confidence: under 60 days of history/)).toBeInTheDocument()
  })

  it('explains a band raised by the history rule and shows the model number', () => {
    render(
      <RiskPanel
        prediction={prediction({ risk_band: 'High', band_source: 'escalated', band_reason: 'Raised to High by history rule', investigation_risk: 0.0282 })}
      />,
    )
    expect(screen.getByTestId('band-escalated')).toHaveTextContent('Raised to High by history rule')
    expect(screen.getByTestId('band-escalated')).toHaveTextContent('2.8%')
  })

  it('says Insufficient data rather than guessing', () => {
    render(<RiskPanel prediction={{ available: false, reason: 'Insufficient data: no model' }} />)
    expect(screen.getByRole('heading', { name: '30-Day Investigation Risk: Insufficient data' })).toBeInTheDocument()
    expect(screen.getByText('Insufficient data: no model')).toBeInTheDocument()
  })
})

describe('ReviewPanel', () => {
  const setup = (detail = caseDetail()) => {
    const onChanged = vi.fn()
    render(<ReviewPanel detail={detail} onChanged={onChanged} />)
    return { onChanged, user: userEvent.setup() }
  }
  const reason = () => screen.getByLabelText('Reason (required)')

  it('keeps the AI recommendation visually separate from the reviewer decision', () => {
    setup()
    const ai = screen.getByTestId('ai-panel')
    const mine = screen.getByTestId('decision-panel')
    expect(within(ai).getByRole('heading', { name: 'AI Recommendation' })).toBeInTheDocument()
    expect(within(mine).getByRole('heading', { name: 'Your Decision' })).toBeInTheDocument()
    expect(ai).not.toContainElement(mine)
    expect(within(ai).getByText(/Assign an investigator for a full review/)).toBeInTheDocument()
    expect(within(ai).getByText(/never denies a claim or blocks a payment/)).toBeInTheDocument()
    expect(within(ai).queryByRole('button')).toBeNull() // the AI panel has no actions
    ;['Open investigation', 'Request more information', 'Dismiss'].forEach((label) =>
      expect(within(mine).getByRole('button', { name: label })).toBeInTheDocument(),
    )
  })

  it('blocks every action without a reason, and sends nothing', async () => {
    const { user } = setup()
    for (const label of ['Open investigation', 'Request more information', 'Dismiss']) {
      await user.click(screen.getByRole('button', { name: label }))
      expect(screen.getByTestId('decision-error')).toHaveTextContent('A reason is required')
    }
    expect(screen.queryByLabelText('Your name')).toBeNull() // the reviewer is whoever is signed in
    await user.type(reason(), 'hmm')
    await user.click(screen.getByRole('button', { name: 'Request more information' }))
    expect(screen.getByTestId('decision-error')).toHaveTextContent('at least 5')
    expect(api.postDecision).not.toHaveBeenCalled()
  })

  it.each([
    ['Open investigation', 'escalate_for_investigation'],
    ['Request more information', 'request_more_information'],
    ['Dismiss', 'dismiss'],
  ])('"%s" records the %s action with the reason', async (label, action) => {
    api.postDecision.mockResolvedValue({ case_status: 'Escalated for investigation' })
    const { user, onChanged } = setup()
    await user.type(reason(), ' Pattern needs checking ')
    await user.click(screen.getByRole('button', { name: label }))
    expect(api.postDecision).toHaveBeenCalledWith('CASE-0001', { action, reason: 'Pattern needs checking' })
    expect(await screen.findByTestId('decision-confirmation')).toHaveTextContent('Escalated for investigation')
    expect(onChanged).toHaveBeenCalledTimes(1)
    expect(reason()).toHaveValue('') // cleared after saving
  })

  it('shows a server error instead of failing silently', async () => {
    api.postDecision.mockRejectedValue({ response: { status: 422, data: { detail: 'reason: too short' } } })
    const { user, onChanged } = setup()
    await user.type(reason(), 'A fine reason')
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(await screen.findByTestId('decision-error')).toHaveTextContent('too short')
    expect(onChanged).not.toHaveBeenCalled()
    expect(reason()).toHaveValue('A fine reason') // the text is kept so nothing is lost
  })

  it('needs a reason for a priority override too', async () => {
    api.postOverride.mockResolvedValue({ override_active: true, priority: 0.99 })
    const { user } = setup()
    await user.click(screen.getByRole('button', { name: 'Set priority' }))
    expect(screen.getByTestId('override-error')).toHaveTextContent('A reason is required')
    expect(api.postOverride).not.toHaveBeenCalled()
    await user.type(screen.getByLabelText('Reason for the override (required)'), 'Records arrive this week')
    await user.click(screen.getByRole('button', { name: 'Set priority' }))
    expect(api.postOverride).toHaveBeenCalledWith('CASE-0001', { priority: 0.77, reason: 'Records arrive this week' })
    expect(await screen.findByTestId('override-confirmation')).toHaveTextContent('Priority set to 0.990')
  })

  it('can clear an active override, and only then offers the button', async () => {
    api.postOverride.mockResolvedValue({ override_active: false, priority: 0.77 })
    const active = caseDetail({ priority: 0.99, override: { priority: 0.99, reason: 'x', reviewer: 'Ben', ts: '2026-10-08T10:00:00Z' } })
    const { user } = setup(active)
    expect(screen.getByText(/yours: 0.990 \(Ben\)/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Reason for the override (required)'), 'Review finished')
    await user.click(screen.getByRole('button', { name: 'Clear override' }))
    expect(api.postOverride).toHaveBeenCalledWith('CASE-0001', { priority: null, reason: 'Review finished' })
  })

  it('lists earlier decisions and overrides, newest first', () => {
    const decisions = [{ audit_id: 5, action: 'dismiss', reason: 'Benign coding', reviewer: 'Ben', ts: '2026-10-08T10:00:00Z' }]
    const overrides = [{ audit_id: 7, action: 'set_priority', reason: 'Urgent', reviewer: 'Asha', ts: '2026-10-08T11:00:00Z' }]
    setup(caseDetail({ decisions, overrides }))
    const items = within(screen.getByTestId('decision-history')).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Priority override')
    expect(items[1]).toHaveTextContent('Dismiss')
    expect(items[1]).toHaveTextContent('Benign coding')
  })
})

describe('queue pieces', () => {
  it('shows detector chips Rules, ML and Graph and which fired', () => {
    render(<DetectorChips fired={['anomaly', 'graph']} />)
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', 'Detectors fired: ML, Graph')
    expect(screen.getByText('Rules')).toHaveAttribute('data-on', 'false')
    expect(screen.getByText('ML')).toHaveAttribute('data-on', 'true')
  })

  it('shows the AI priority beside the reviewer override', () => {
    render(<PriorityBar priority={0.99} aiPriority={0.4} override={{ reviewer: 'Asha', reason: 'Urgent' }} />)
    expect(screen.getByText('AI 0.40 → Asha 0.99')).toBeInTheDocument()
    expect(screen.getByRole('meter', { name: 'Priority' })).toHaveAttribute('aria-valuenow', '0.99')
  })

  it('summarises the five factors for assistive tech', () => {
    render(<FactorBars factors={factors()} />)
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', expect.stringContaining('Risk 1.00, Dollars 1.00, Members 0.40'))
  })

  it('draws the scheduled and backlog dividers around the ranked rows', () => {
    render(
      <MemoryRouter>
        <QueueTable queue={queueData()} />
      </MemoryRouter>,
    )
    const rows = screen.getAllByTestId('queue-row').map((r) => r.dataset.case)
    expect(rows).toEqual(['CASE-0001', 'CASE-0003', 'CASE-0005'])
    expect(screen.getByTestId('divider-scheduled')).toHaveTextContent('Scheduled · 2 cases · 13 h of 40 h')
    expect(screen.getByTestId('divider-backlog')).toHaveTextContent('Backlog · 1 case waiting for capacity')
    const first = screen.getAllByTestId('queue-row')[0]
    expect(first).toHaveTextContent('Ring')
    expect(first).toHaveTextContent('Rs 345,703')
    expect(screen.getAllByTestId('queue-row')[1]).toHaveTextContent('Provider')
  })

  it('says so when nothing fits or nothing waits', () => {
    const { rerender } = render(<MemoryRouter><QueueTable queue={queueData({ scheduled: [], scheduled_hours: 0 })} /></MemoryRouter>)
    expect(screen.getByText('No case fits in this many team hours.')).toBeInTheDocument()
    rerender(<MemoryRouter><QueueTable queue={queueData({ backlog: [] })} /></MemoryRouter>)
    expect(screen.getByText(/Nothing is waiting/)).toBeInTheDocument()
  })

  it('links each case title to its page and notes a decided status', () => {
    render(
      <MemoryRouter>
        <QueueTable queue={queueData({ scheduled: [queueItem({ status: 'Escalated for investigation' }), providerItem()] })} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: /Referral network/ })).toHaveAttribute('href', '/cases/CASE-0001')
    expect(screen.getByText(/Escalated for investigation/)).toBeInTheDocument()
  })
})

describe('WeightControls', () => {
  it('shows shares that add up to 100% and lets the reviewer reset', async () => {
    const onChange = vi.fn()
    render(<WeightControls raw={{ ...DEFAULT_WEIGHTS, risk: 60 }} onChange={onChange} open onToggle={vi.fn()} />)
    const shares = ['risk', 'dollars', 'impact', 'severity', 'evidence'].map((k) => Number(screen.getByTestId(`weight-${k}`).textContent.replace('%', '')))
    expect(shares.reduce((a, b) => a + b, 0)).toBe(100)
    await userEvent.click(screen.getByRole('button', { name: 'Reset to defaults' }))
    expect(onChange).toHaveBeenCalledWith(DEFAULT_WEIGHTS)
  })

  it('collapses and expands, and warns when every weight is zero', () => {
    const zero = { risk: 0, dollars: 0, impact: 0, severity: 0, evidence: 0 }
    const { rerender } = render(<WeightControls raw={zero} onChange={vi.fn()} open={false} onToggle={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Priority weights/ })).toHaveAttribute('aria-expanded', 'false')
    rerender(<WeightControls raw={zero} onChange={vi.fn()} open onToggle={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Priority weights/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('alert')).toHaveTextContent('at least one weight')
  })
})

describe('Timeline and FindingsChart', () => {
  it('lists dated events with citations that can be clicked', async () => {
    const onCite = vi.fn()
    render(<Timeline entries={caseDetail().timeline} onCite={onCite} />)
    expect(screen.getAllByTestId('timeline-entry')).toHaveLength(2)
    expect(screen.getByText('2026-01-02 to 2026-01-26')).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: '[E2]' })[0])
    expect(onCite).toHaveBeenCalledWith('E2')
  })

  it('says so when there is nothing to show', () => {
    render(<Timeline entries={[]} onCite={vi.fn()} />)
    expect(screen.getByText(/No dated events/)).toBeInTheDocument()
  })

  it('summarises the chart for assistive tech and offers a table view', async () => {
    // jsdom has no layout, so the chart library warns about a zero-size container; a real browser does not
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    render(<FindingsChart data={{ duplicate: 20, ring: 1 }} />)
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', 'Findings per detector: Duplicate billing 20, Referral network 1')
    await userEvent.click(screen.getByRole('button', { name: 'View as table' }))
    const rows = screen.getAllByRole('row')
    expect(rows[1]).toHaveTextContent('Duplicate billing')
    expect(rows[1]).toHaveTextContent('20')
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('handles an empty result', () => {
    render(<FindingsChart data={{}} />)
    expect(screen.getByText('No findings yet.')).toBeInTheDocument()
  })
})

describe('evidence rows endpoint shape used by the UI', () => {
  it('has the fields the table needs', () => {
    expect(Object.keys(evidenceRows().claims[0])).toEqual(
      expect.arrayContaining(['claim_id', 'service_date', 'member_id', 'provider_id', 'facility_id', 'procedure_code', 'billed_amount']),
    )
  })
})
