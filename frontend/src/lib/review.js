export const ACTIONS = [
  { action: 'escalate_for_investigation', label: 'Open investigation', variant: 'primary' },
  { action: 'request_more_information', label: 'Request records', variant: 'default' },
  { action: 'dismiss', label: 'Dismiss', variant: 'default' },
]

export const ACTION_LABELS = {
  ...Object.fromEntries(ACTIONS.map((a) => [a.action, a.label])),
  monitor: 'Monitor',
  set_priority: 'Priority override',
  clear_priority: 'Override cleared',
}

export const MIN_REASON = 5
export const MIN_REVIEWER = 2

/** Every action needs a named reviewer and a reason; returns the message to show, or null. */
export function validateEntry(reviewer, reason) {
  if (reviewer.trim().length < MIN_REVIEWER) return 'Enter your name before recording anything.'
  if (reason.trim().length < MIN_REASON) return `A reason is required (at least ${MIN_REASON} characters).`
  return null
}
