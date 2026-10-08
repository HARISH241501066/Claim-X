import { describe, expect, it } from 'vitest'
import { API_URL, errorMessage } from '../api'
import { count, dateRange, hours, riskPercent, rupees } from './format'
import { ACTIONS, validateReason } from './review'
import { HORIZONS, isTrained, tooltipText } from './risk'
import { DEFAULT_WEIGHTS, WEIGHT_KEYS, normalizeWeights, percentShares, weightsParam } from './weights'

const total = (obj) => Object.values(obj).reduce((a, b) => a + b, 0)

describe('normalizeWeights', () => {
  it('turns the default sliders into the API defaults', () => {
    expect(normalizeWeights(DEFAULT_WEIGHTS)).toEqual({ risk: 0.3, dollars: 0.25, impact: 0.15, severity: 0.15, evidence: 0.15 })
  })

  it('always adds up to exactly 1, whatever the sliders say', () => {
    let seed = 7
    const next = () => ((seed = (seed * 16807) % 2147483647) / 2147483647)
    for (let i = 0; i < 300; i++) {
      const raw = Object.fromEntries(WEIGHT_KEYS.map((k) => [k, Math.round(next() * 100)]))
      const weights = normalizeWeights(raw)
      if (weights) expect(Math.abs(total(weights) - 1)).toBeLessThan(1e-9) // the API tolerance
    }
  })

  it('spreads a remainder fairly and keeps zero sliders at zero', () => {
    const w = normalizeWeights({ risk: 1, dollars: 1, impact: 1, severity: 0, evidence: 0 })
    expect(w.severity).toBe(0)
    expect(w.evidence).toBe(0)
    expect(Math.abs(total(w) - 1)).toBeLessThan(1e-12)
    expect(Math.max(w.risk, w.dollars, w.impact) - Math.min(w.risk, w.dollars, w.impact)).toBeLessThanOrEqual(0.001 + 1e-9)
  })

  it('returns null when nothing is weighted, and ignores negatives and junk', () => {
    expect(normalizeWeights({ risk: 0, dollars: 0, impact: 0, severity: 0, evidence: 0 })).toBeNull()
    expect(normalizeWeights({ risk: -5, dollars: 'x', impact: NaN, severity: 0, evidence: 0 })).toBeNull()
    expect(normalizeWeights({ ...DEFAULT_WEIGHTS, risk: -50 }).risk).toBe(0)
  })

  it('writes the parameter in the format the queue endpoint reads', () => {
    expect(weightsParam(normalizeWeights(DEFAULT_WEIGHTS))).toBe('risk:0.3,dollars:0.25,impact:0.15,severity:0.15,evidence:0.15')
  })
})

describe('percentShares', () => {
  it('shows whole percentages that add up to 100 (the case that once showed 101)', () => {
    const shares = percentShares({ ...DEFAULT_WEIGHTS, risk: 60 })
    expect(total(shares)).toBe(100)
    expect(shares.risk).toBeGreaterThan(DEFAULT_WEIGHTS.risk)
  })

  it('always totals 100', () => {
    for (const risk of [0, 1, 7, 33, 60, 99, 100]) {
      for (const dollars of [0, 3, 50, 100]) {
        const shares = percentShares({ ...DEFAULT_WEIGHTS, risk, dollars })
        expect(total(shares)).toBe(100)
      }
    }
  })

  it('is null when nothing is weighted', () => {
    expect(percentShares({ risk: 0, dollars: 0, impact: 0, severity: 0, evidence: 0 })).toBeNull()
  })
})

describe('formatting', () => {
  it('writes rupees like the briefs do', () => {
    expect(rupees(345703)).toBe('Rs 345,703')
    expect(rupees(0)).toBe('Rs 0')
    expect(rupees(undefined)).toBe('Rs 0')
    expect(count(5000)).toBe('5,000')
  })

  it('shows risk as a readable percentage', () => {
    expect(riskPercent(0.0282)).toBe('2.8%')
    expect(riskPercent(0.31)).toBe('31%')
    expect(riskPercent(0)).toBe('0.0%')
    expect(riskPercent(1)).toBe('100%')
  })

  it('formats date ranges and hours', () => {
    expect(dateRange('2026-01-02', '2026-01-02')).toBe('2026-01-02')
    expect(dateRange('2026-01-02', '2026-01-26')).toBe('2026-01-02 to 2026-01-26')
    expect(hours(9)).toBe('9 h')
    expect(hours(37.5)).toBe('37.5 h')
  })
})

describe('review rules', () => {
  it('needs a name and a reason of at least five characters', () => {
    expect(validateReason('')).toMatch(/reason is required/)
    expect(validateReason('    ')).toMatch(/reason is required/)
    expect(validateReason('abcd')).toMatch(/at least 5/)
    expect(validateReason('abcde')).toBeNull()
  })

  it('offers only the three review actions, none of which denies or blocks anything', () => {
    expect(ACTIONS.map((a) => a.label)).toEqual(['Open investigation', 'Request more information', 'Dismiss'])
    expect(ACTIONS.map((a) => a.action).join(' ')).not.toMatch(/deny|block|reject|pay/)
  })
})

describe('risk panel text', () => {
  it('uses the exact caveat, with the window filled in', () => {
    expect(tooltipText(30)).toBe(
      'Estimated likelihood of a confirmed investigation within 30 days, based on synthetic history. Not a finding of fraud.',
    )
    expect(tooltipText(90)).toContain('within 90 days')
  })

  it('only the 30-day model is trained', () => {
    expect(HORIZONS).toEqual([30, 60, 90])
    expect(HORIZONS.filter(isTrained)).toEqual([30])
  })
})

describe('errorMessage', () => {
  it('explains a starting server', () => {
    expect(errorMessage({ response: { status: 503, data: { detail: 'x' } } })).toMatch(/still starting/)
  })

  it('uses the server detail when there is one', () => {
    expect(errorMessage({ response: { status: 404, data: { detail: 'Unknown case CASE-9' } } })).toBe('Unknown case CASE-9')
  })

  it('reads the first validation message', () => {
    const err = { response: { status: 422, data: { detail: [{ loc: ['body', 'reason'], msg: 'String should have at least 5 characters' }] } } }
    expect(errorMessage(err)).toBe('reason: String should have at least 5 characters')
  })

  it('falls back to the status, a timeout notice, or a network notice', () => {
    expect(errorMessage({ response: { status: 500, data: {} } })).toMatch(/500/)
    expect(errorMessage({ code: 'ECONNABORTED' })).toMatch(/too long/)
    expect(errorMessage(new Error('Network Error'))).toContain(API_URL)
  })
})
