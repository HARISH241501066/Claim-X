// Small, realistic API payloads for the component tests.

export const factors = (over = {}) => ({ risk: 1, dollars: 1, impact: 0.4, severity: 0.75, evidence: 0.67, ...over })

export const queueItem = (over = {}) => ({
  rank: 1,
  case_id: 'CASE-0001',
  title: 'Referral network: RING-01 (5 linked entities)',
  case_type: 'ring',
  primary_entity: 'RING-01',
  priority: 0.77,
  ai_priority: 0.77,
  override: null,
  factors: factors(),
  flagged_amount: 345703,
  n_members: 15,
  detectors_fired: ['anomaly', 'graph'],
  investigation_risk: 0.01,
  investigation_band: 'Low',
  effort_hours: 9,
  cumulative_hours: 9,
  queue: 'scheduled',
  status: 'Awaiting human review',
  summary: 'Referral network summary.',
  ...over,
})

export const providerItem = (over = {}) =>
  queueItem({
    rank: 2,
    case_id: 'CASE-0003',
    title: 'Upcoding pattern: PRV-005',
    case_type: 'provider',
    primary_entity: 'PRV-005',
    priority: 0.62,
    ai_priority: 0.62,
    factors: factors({ risk: 0.5, dollars: 0.6 }),
    flagged_amount: 210589,
    n_members: 42,
    detectors_fired: ['rules'],
    effort_hours: 4,
    cumulative_hours: 13,
    ...over,
  })

export const queueData = (over = {}) => ({
  capacity_hours: 40,
  weights: { risk: 0.3, dollars: 0.25, impact: 0.15, severity: 0.15, evidence: 0.15 },
  scheduled_hours: 13,
  scheduled: [queueItem(), providerItem()],
  backlog: [providerItem({ rank: 3, case_id: 'CASE-0005', title: 'Duplicate billing: PRV-007', primary_entity: 'PRV-007', queue: 'backlog', priority: 0.34, ai_priority: 0.34 })],
  decided_excluded: 0,
  ...over,
})

export const overviewData = (over = {}) => ({
  claims: 5000,
  findings: 57,
  cases: 20,
  dollars_at_risk: 924113,
  findings_per_rule: { duplicate: 20, unbundling: 12, phantom: 10, utilization: 10, anomaly: 2, ring: 1 },
  cases_awaiting_review: 20,
  cases_decided: 0,
  cases_scheduled: 8,
  cases_backlog: 12,
  scheduled_hours: 37,
  team_hours: 40,
  ...over,
})

export const healthData = (over = {}) => ({
  status: 'ok',
  ready: true,
  started_at: '2026-10-08T11:44:29Z',
  finished_at: '2026-10-08T11:44:34Z',
  total_seconds: 4.4,
  stages: [{ name: 'data', seconds: 0.2, status: 'ok', error: null }],
  ...over,
})

export const prediction = (over = {}) => ({
  available: true,
  reason: null,
  horizon_days: 30,
  provider_id: 'PRV-A01',
  investigation_risk: 0.0282,
  risk_band: 'Low',
  band_source: 'model',
  band_reason: 'Model estimate 0.03',
  top_drivers: ['Services per member per month 3', 'Claims per day 1.49'],
  history_days: 181,
  low_confidence: false,
  ...over,
})

export const finding = (over = {}) => ({
  key: 'E1',
  finding_id: 'FND-000055',
  detector: 'anomaly',
  entity_id: 'PRV-A01',
  severity: 'high',
  score: 1,
  reason: 'Provider PRV-A01 has an unusual overall profile versus peers. Suspicious pattern that warrants review.',
  evidence_ids: ['CLM-000014', 'CLM-000031', 'CLM-000032', 'CLM-000048', 'CLM-000049', 'CLM-000050'],
  ...over,
})

export const caseDetail = (over = {}) => ({
  case_id: 'CASE-0001',
  title: 'Referral network: RING-01 (5 linked entities)',
  case_type: 'ring',
  primary_entity: 'RING-01',
  entity_ids: ['PRV-A01', 'FAC-B01', 'FAC-C01', 'OWN-001', 'OWN-002'],
  rank: 1,
  priority: 0.77,
  ai_priority: 0.77,
  override: null,
  recommended_action: { tier: 'full-review', text: 'Assign an investigator for a full review soon. No claim should be denied and no payment blocked on the basis of this brief alone.' },
  overrides: [],
  queue: 'scheduled',
  status: 'Awaiting human review',
  flagged_amount: 345703,
  affected_members: ['MEM-001', 'MEM-002'],
  detectors_fired: ['anomaly', 'graph'],
  summary: 'Referral network summary.',
  findings: [
    finding(),
    finding({ key: 'E2', finding_id: 'FND-000057', detector: 'ring', entity_id: 'RING-01', score: 0.76, reason: 'Referral network of FAC-B01. Suspicious network that warrants review.', evidence_ids: ['CLM-000031', 'CLM-000048'] }),
  ],
  timeline: [
    { date: '2026-01-02', end_date: '2026-01-26', kind: 'ring claims', description: '30 ring claims worth Rs 58,435 in 2026-01', evidence_keys: ['E2'], count: 30, amount: 58435 },
    { date: '2026-02-02', end_date: '2026-02-23', kind: 'ring claims', description: '30 ring claims worth Rs 56,922 in 2026-02', evidence_keys: ['E2'], count: 30, amount: 56922 },
  ],
  prediction: prediction(),
  confidence: { level: 'Medium', reasons: ['2 of 3 detector groups fired', 'prediction band Low'] },
  limitations: ['Synthetic data: all records are simulated.'],
  decisions: [],
  ...over,
})

export const graphData = () => ({
  case_id: 'CASE-0001',
  nodes: [
    { id: 'PRV-A01', type: 'provider', suspicious: true, label: 'PRV-A01 (General Medicine)', count: null },
    { id: 'FAC-B01', type: 'facility', suspicious: true, label: 'FAC-B01 (lab)', count: null },
    { id: 'MEM-001', type: 'member', suspicious: false, label: 'MEM-001', count: null },
  ],
  links: [
    { source: 'PRV-A01', target: 'FAC-B01', type: 'referred_to', weight: 90, suspicious: true },
    { source: 'PRV-A01', target: 'MEM-001', type: 'billed_for', weight: 3, suspicious: false },
  ],
  member_count: 1,
  members_collapsed: false,
})

export const briefData = (over = {}) => ({
  case_id: 'CASE-0001',
  horizon_days: 30,
  source: 'template',
  fallback_reason: 'no LLM_API_KEY is set',
  model: null,
  cached: false,
  brief: [
    '# Investigation brief: CASE-0001',
    '',
    '## Summary',
    'Two detector groups fired: anomaly [E1]; graph [E2].',
    '',
    '## Evidence',
    '- [E1] anomaly finding. A made-up citation [E9] stays plain text.',
    '- [E2] ring finding.',
    '',
    'Final decision rests with the assigned investigator.',
  ].join('\n'),
  ...over,
})

export const evidenceRows = (key = 'E2') => ({
  case_id: 'CASE-0001',
  key,
  finding_id: 'FND-000057',
  detector: 'ring',
  scope: 'claim-specific',
  total_claims: 2,
  shown: 2,
  claims: [
    { claim_id: 'CLM-000031', service_date: '2026-01-02', member_id: 'MEM-165', provider_id: 'PRV-A01', facility_id: 'FAC-C01', referring_provider_id: 'PRV-A01', procedure_code: 'RAD-XRAY', code_level: null, billed_amount: 1662, claim_type: 'outpatient' },
    { claim_id: 'CLM-000048', service_date: '2026-01-03', member_id: 'MEM-165', provider_id: 'PRV-A01', facility_id: 'FAC-B01', referring_provider_id: 'PRV-A01', procedure_code: 'CON-GM-3', code_level: 3, billed_amount: 2139, claim_type: 'outpatient' },
  ],
  linked_records: [],
  note: null,
})
