export const WEIGHT_KEYS = ['risk', 'dollars', 'impact', 'severity', 'evidence']

export const WEIGHT_LABELS = {
  risk: 'Risk',
  dollars: 'Dollars',
  impact: 'Members affected',
  severity: 'Severity',
  evidence: 'Evidence breadth',
}

// The same defaults the API uses, as whole percentages.
export const DEFAULT_WEIGHTS = { risk: 30, dollars: 25, impact: 15, severity: 15, evidence: 15 }

/**
 * Turn raw slider values into weights that sum to exactly 1 (in thousandths, using the
 * largest-remainder method). Returns null when every slider is zero.
 */
export function normalizeWeights(raw) {
  const total = WEIGHT_KEYS.reduce((sum, k) => sum + Math.max(0, Number(raw[k]) || 0), 0)
  if (total <= 0) return null
  const exact = WEIGHT_KEYS.map((k) => (Math.max(0, Number(raw[k]) || 0) / total) * 1000)
  const floors = exact.map(Math.floor)
  let leftover = 1000 - floors.reduce((a, b) => a + b, 0)
  const order = exact
    .map((value, i) => ({ i, remainder: value - floors[i] }))
    .sort((a, b) => b.remainder - a.remainder || a.i - b.i)
  for (const { i } of order) {
    if (leftover <= 0) break
    floors[i] += 1
    leftover -= 1
  }
  return Object.fromEntries(WEIGHT_KEYS.map((k, i) => [k, floors[i] / 1000]))
}

/** Whole-number shares for display that always add up to exactly 100. */
export function percentShares(raw) {
  const weights = normalizeWeights(raw)
  if (!weights) return null
  const exact = WEIGHT_KEYS.map((k) => weights[k] * 100)
  const floors = exact.map(Math.floor)
  let leftover = 100 - floors.reduce((a, b) => a + b, 0)
  const order = exact
    .map((value, i) => ({ i, remainder: value - floors[i] }))
    .sort((a, b) => b.remainder - a.remainder || a.i - b.i)
  for (const { i } of order) {
    if (leftover <= 0) break
    floors[i] += 1
    leftover -= 1
  }
  return Object.fromEntries(WEIGHT_KEYS.map((k, i) => [k, floors[i]]))
}

/** "risk:0.3,dollars:0.25,..." as the /queue endpoint expects. */
export function weightsParam(weights) {
  return WEIGHT_KEYS.map((k) => `${k}:${weights[k]}`).join(',')
}
