const number = new Intl.NumberFormat('en-US')

export const count = (n) => number.format(n ?? 0)

/** Rupees, written the way the briefs write them: Rs 345,703 */
export const rupees = (n) => `Rs ${number.format(Math.round(n ?? 0))}`

export const priority = (x) => (x ?? 0).toFixed(3)

/** 0.0282 -> "2.8%", 0.31 -> "31%". */
export function riskPercent(x) {
  const p = (x ?? 0) * 100
  return p >= 10 ? `${Math.round(p)}%` : `${p.toFixed(1)}%`
}

export function dateRange(start, end) {
  return start === end ? start : `${start} to ${end}`
}

export function when(iso) {
  return iso ? iso.replace('T', ' ').replace('Z', ' UTC') : ''
}

export const hours = (h) => `${Number.isInteger(h) ? h : h.toFixed(1)} h`
