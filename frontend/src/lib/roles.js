// What each role is called, where it starts and its navigation. The API enforces access; this only reflects it.
export const ROLE_LABELS = { admin: 'Admin', team_lead: 'Team lead', investigator: 'Investigator' }

export const HOME = { investigator: '/my-cases', team_lead: '/unit-queue', admin: '/' }

export function navFor(role) {
  if (role === 'investigator') return [{ to: '/my-cases', label: 'My Cases' }, { to: '/unit-queue', label: 'Unit Queue' }]
  if (role === 'team_lead') return [{ to: '/unit-queue', label: 'Unit Queue' }, { to: '/workload', label: 'Team Workload' }]
  return [
    { to: '/', label: 'Overview', end: true },
    { to: '/queue', label: 'All Cases' },
    { to: '/system', label: 'System' },
  ]
}
