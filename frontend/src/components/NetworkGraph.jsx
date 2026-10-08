import { useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { COLORS, NODE_COLORS, NODE_LABELS } from '../theme'

const HEIGHT = 420
const NODE_SIZE = { provider: 5, facility: 5, owner: 5, member: 2, member_group: 8 }

function Legend({ types }) {
  return (
    <ul className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2" aria-label="Legend">
      {types.map((t) => (
        <li key={t} className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: NODE_COLORS[t] }} />
          {NODE_LABELS[t] ?? t}
        </li>
      ))}
      <li className="flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block h-0.5 w-5" style={{ background: COLORS.flag }} />
        Suspicious link
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full border-2" style={{ borderColor: COLORS.flag }} />
        Flagged entity
      </li>
    </ul>
  )
}

/** The case network: nodes coloured by type, suspicious links red, plus a table view. */
export default function NetworkGraph({ graph }) {
  const wrap = useRef(null)
  const fg = useRef(null)
  const [width, setWidth] = useState(640)

  useEffect(() => {
    const el = wrap.current
    if (!el) return undefined
    const measure = () => setWidth(Math.max(280, Math.floor(el.clientWidth)))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // The graph library rewrites links in place, so give it its own copy.
  const data = useMemo(
    () => ({ nodes: graph.nodes.map((n) => ({ ...n })), links: graph.links.map((l) => ({ ...l })) }),
    [graph],
  )
  const types = [...new Set(graph.nodes.map((n) => n.type))]

  // Push nodes apart a little so the network fills the canvas.
  useEffect(() => {
    fg.current?.d3Force?.('charge')?.strength(-90)
  }, [data])

  return (
    <div>
      <div ref={wrap} className="overflow-hidden rounded-lg border border-line" data-testid="network-graph">
        <ForceGraph2D
          ref={fg}
          graphData={data}
          width={width}
          height={HEIGHT}
          backgroundColor={COLORS.surface}
          nodeId="id"
          nodeLabel={(n) => `${n.label}${n.suspicious ? ' (flagged)' : ''}`}
          nodeColor={(n) => NODE_COLORS[n.type] ?? COLORS.muted}
          nodeVal={(n) => NODE_SIZE[n.type] ?? 3}
          nodeRelSize={4}
          linkColor={(l) => (l.suspicious ? COLORS.flag : '#4a4a46')}
          linkWidth={(l) => (l.suspicious ? 2 : 1)}
          nodeCanvasObjectMode={() => 'after'}
          nodeCanvasObject={(node, ctx) => {
            if (!node.suspicious) return
            const r = Math.sqrt(NODE_SIZE[node.type] ?? 3) * 4 + 2.5
            ctx.beginPath()
            ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
            ctx.strokeStyle = COLORS.flag
            ctx.lineWidth = 1.5
            ctx.stroke()
          }}
          cooldownTicks={90}
          onEngineStop={() => fg.current?.zoomToFit?.(300, 40)}
        />
      </div>
      <Legend types={types} />
      <details className="mt-3 text-xs text-ink-2">
        <summary className="cursor-pointer text-ink">Table view ({graph.nodes.length} nodes, {graph.links.length} links)</summary>
        <div className="mt-2 grid gap-4 md:grid-cols-2">
          <table className="w-full text-left">
            <caption className="mb-1 text-left text-muted">Nodes</caption>
            <tbody>
              {graph.nodes.map((n) => (
                <tr key={n.id} className="border-t border-line">
                  <td className="py-0.5">{n.label}</td>
                  <td className="py-0.5 text-muted">{NODE_LABELS[n.type] ?? n.type}</td>
                  <td className="py-0.5 text-flag">{n.suspicious ? 'flagged' : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <table className="w-full text-left">
            <caption className="mb-1 text-left text-muted">Links</caption>
            <tbody>
              {graph.links.map((l, i) => (
                <tr key={i} className="border-t border-line">
                  <td className="py-0.5">{l.source} → {l.target}</td>
                  <td className="py-0.5 text-muted">{l.type.replace('_', ' ')}</td>
                  <td className="py-0.5 text-flag">{l.suspicious ? 'suspicious' : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  )
}
