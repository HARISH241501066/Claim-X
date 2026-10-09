import { useEffect } from 'react'
import { getEvidence } from '../api'
import { rupees } from '../lib/format'
import { useApi } from '../lib/useApi'
import Async from './Async'
import { Badge, Button } from './ui'

const SEVERITY_TONE = { high: 'serious', medium: 'warn', low: 'muted', critical: 'critical' }
const PREVIEW_IDS = 4

function EvidenceRows({ caseId, evidenceKey }) {
  const state = useApi((signal) => getEvidence(caseId, evidenceKey, signal), [caseId, evidenceKey])
  return (
    <div className="mt-3 border-t border-line pt-3">
      <Async state={state} label="Loading claim rows" rows={4}>
        {(data) => (
          <div data-testid={`rows-${evidenceKey}`}>
            {data.note && <p className="mb-2 text-xs text-warn">{data.note}</p>}
            {data.linked_records.length > 0 && (
              <ul className="mb-2 space-y-1 text-xs text-ink-2">
                {data.linked_records.map((r) => (
                  <li key={r.id}>
                    <span className="font-medium text-ink">{r.id}</span> · {r.kind}: {r.description}
                  </li>
                ))}
              </ul>
            )}
            {data.claims.length === 0 ? (
              <p className="text-xs text-muted">This item cites no claim rows (it cites linked records only).</p>
            ) : (
              <div className="max-h-72 overflow-auto rounded-md border border-line">
                <table className="w-full min-w-[40rem] text-left text-xs">
                  <thead className="sticky top-0 bg-surface-2 text-muted">
                    <tr>
                      {['Claim', 'Date', 'Member', 'Provider', 'Facility', 'Code', 'Amount'].map((h) => (
                        <th key={h} scope="col" className="px-2 py-1.5 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.claims.map((c) => (
                      <tr key={c.claim_id} className="border-t border-line text-ink-2" data-testid="claim-row">
                        <td className="px-2 py-1 font-medium text-ink">{c.claim_id}</td>
                        <td className="px-2 py-1 tabular-nums">{c.service_date}</td>
                        <td className="px-2 py-1">{c.member_id}</td>
                        <td className="px-2 py-1">{c.provider_id}</td>
                        <td className="px-2 py-1">{c.facility_id}</td>
                        <td className="px-2 py-1">
                          {c.procedure_code}
                          {c.code_level ? ` (L${c.code_level})` : ''}
                        </td>
                        <td className="px-2 py-1 text-right tabular-nums">{rupees(c.billed_amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="mt-1 text-xs text-muted">
              Showing {data.shown} of {data.total_claims} claims.
            </p>
          </div>
        )}
      </Async>
    </div>
  )
}

/** Findings keyed E1, E2 … with their evidence IDs; selecting one shows the claim rows behind it. */
export default function EvidenceList({ caseId, findings, selectedKey, onSelect }) {
  useEffect(() => {
    if (!selectedKey) return
    document.getElementById(`evidence-${selectedKey}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [selectedKey])

  if (!findings.length) return <p className="text-sm text-muted">No findings are attached to this case.</p>
  return (
    <ul className="space-y-3">
      {findings.map((f) => {
        const open = selectedKey === f.key
        return (
          <li
            key={f.finding_id}
            id={`evidence-${f.key}`}
            data-testid="evidence-item"
            data-selected={open}
            aria-current={open ? 'true' : undefined}
            className={`rounded-lg border p-3 ${open ? 'border-accent bg-accent/5' : 'border-line'}`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="accent" className="font-semibold">{f.key}</Badge>
              <span className="text-sm font-medium text-ink" data-testid="rule-label">{f.rule_label ?? f.detector.replace('_', ' ')}</span>
              {f.rule_kind && (
                <span className="rounded border border-line px-1.5 py-0.5 text-xs text-ink-2" data-testid="rule-kind">
                  {f.rule_kind}
                </span>
              )}
              <Badge tone={SEVERITY_TONE[f.severity] ?? 'neutral'}>{f.severity}</Badge>
              <span className="text-xs text-muted">score {f.score.toFixed(2)} · {f.finding_id} · {f.entity_id}</span>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-ink-2">{f.reason}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted">Evidence IDs ({f.evidence_ids.length}):</span>
              {f.evidence_ids.slice(0, PREVIEW_IDS).map((id) => (
                <code key={id} className="rounded bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-2">{id}</code>
              ))}
              {f.evidence_ids.length > PREVIEW_IDS && (
                <span className="text-xs text-muted">+{f.evidence_ids.length - PREVIEW_IDS} more</span>
              )}
              <Button
                variant="quiet"
                className="ml-auto"
                aria-expanded={open}
                onClick={() => onSelect(open ? null : f.key)}
              >
                {open ? 'Hide claim rows' : 'Show claim rows'}
              </Button>
            </div>
            {open && <EvidenceRows caseId={caseId} evidenceKey={f.key} />}
          </li>
        )
      })}
    </ul>
  )
}
