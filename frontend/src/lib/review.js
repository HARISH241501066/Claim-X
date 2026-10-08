export const ACTIONS = [
  { action: 'escalate_for_investigation', label: 'Open investigation', variant: 'primary' },
  { action: 'request_more_information', label: 'Request more information', variant: 'default' },
  { action: 'dismiss', label: 'Dismiss', variant: 'default' },
]

export const ACTION_LABELS = {
  ...Object.fromEntries(ACTIONS.map((a) => [a.action, a.label])),
  monitor: 'Monitor',
  set_priority: 'Priority override',
  clear_priority: 'Override cleared',
}

export const MIN_REASON = 5

/** Every action needs a reason (the person is the signed-in user); returns the message to show, or null. */
export function validateReason(reason) {
  if (reason.trim().length < MIN_REASON) return `A reason is required (at least ${MIN_REASON} characters).`
  return null
}
