import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { RULE_LABELS } from '../lib/labels'
import { COLORS } from '../theme'
import { Button } from './ui'

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const { label, count } = payload[0].payload
  return (
    <div className="rounded-md border border-axis bg-surface-2 px-3 py-2 text-xs shadow-lg">
      <p className="font-medium text-ink">{label}</p>
      <p className="text-ink-2">
        {count} {count === 1 ? 'finding' : 'findings'}
      </p>
    </div>
  )
}

/** Findings per detector as thin horizontal bars, with a table view for assistive tech. */
export default function FindingsChart({ data }) {
  const [asTable, setAsTable] = useState(false)
  const rows = Object.entries(data ?? {})
    .map(([rule, count]) => ({ rule, label: RULE_LABELS[rule] ?? rule, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))

  if (!rows.length) return <p className="text-sm text-muted">No findings yet.</p>
  const height = rows.length * 34 + 36

  return (
    <div>
      <div className="mb-2 flex justify-end">
        <Button variant="quiet" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          {asTable ? 'View as chart' : 'View as table'}
        </Button>
      </div>
      {asTable ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="py-1 font-medium">Detector</th>
              <th className="py-1 text-right font-medium">Findings</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.rule} className="border-t border-line">
                <td className="py-1.5 text-ink-2">{r.label}</td>
                <td className="py-1.5 text-right tabular-nums text-ink">{r.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div role="img" aria-label={`Findings per detector: ${rows.map((r) => `${r.label} ${r.count}`).join(', ')}`} style={{ height }}>
          <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height }}>
            <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 8 }}>
              <CartesianGrid horizontal={false} stroke={COLORS.line} />
              <XAxis
                type="number"
                allowDecimals={false}
                tick={{ fill: COLORS.muted, fontSize: 12 }}
                axisLine={{ stroke: COLORS.axis }}
                tickLine={false}
              />
              <YAxis
                type="category"
                dataKey="label"
                width={150}
                tick={{ fill: COLORS.ink2, fontSize: 12 }}
                axisLine={{ stroke: COLORS.axis }}
                tickLine={false}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
              <Bar dataKey="count" fill={COLORS.accent} radius={[0, 4, 4, 0]} barSize={18} isAnimationActive={false}>
                <LabelList dataKey="count" position="right" fill={COLORS.ink2} fontSize={12} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
