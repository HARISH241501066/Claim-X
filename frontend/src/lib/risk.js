export const HORIZONS = [30, 60, 90]

/** A window can be chosen when the API has an estimate for it (without `predictions`, only 30 days). */
export const isTrained = (horizon, predictions) =>
  predictions ? Boolean(predictions[String(horizon)]?.available) : horizon === 30

/** The estimate for the chosen window: its own, or the single 30-day one when no map is given. */
export const forHorizon = (horizon, prediction, predictions) =>
  predictions ? (predictions[String(horizon)] ?? { available: false, reason: 'Insufficient data' }) : prediction

export function tooltipText(horizon) {
  return `Estimated likelihood of a confirmed investigation within ${horizon} days, based on synthetic history. Not a finding of fraud.`
}
