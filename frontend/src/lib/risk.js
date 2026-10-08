export const HORIZONS = [30, 60, 90]

// Only the 30-day model has been trained; 60 and 90 are shown but disabled.
export const isTrained = (horizon) => horizon === 30

export function tooltipText(horizon) {
  return `Estimated likelihood of a confirmed investigation within ${horizon} days, based on synthetic history. Not a finding of fraud.`
}
