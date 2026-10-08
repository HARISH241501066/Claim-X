import { Badge } from './ui'

const CITATION = /(\[E\d+\])/g
const BOLD = /(\*\*[^*\n]+\*\*)/g

function citations(text, validKeys, onCite) {
  return text.split(CITATION).map((part, i) => {
    const match = /^\[(E\d+)\]$/.exec(part)
    if (!match || !validKeys.has(match[1])) return part
    const key = match[1]
    return (
      <button
        key={i}
        type="button"
        data-cite={key}
        onClick={() => onCite(key)}
        title={`Show evidence ${key}`}
        className="mx-0.5 rounded border border-accent/50 bg-accent/10 px-1 text-xs font-medium text-accent hover:bg-accent/25"
      >
        [{key}]
      </button>
    )
  })
}

/** Inline text: **bold**, and [E#] markers (known keys become buttons, others stay plain text). */
function inline(text, validKeys, onCite) {
  return text.split(BOLD).map((part, i) => {
    const bold = /^\*\*([^*\n]+)\*\*$/.exec(part)
    return bold ? (
      <strong key={i} className="font-semibold text-ink">
        {citations(bold[1], validKeys, onCite)}
      </strong>
    ) : (
      <span key={i}>{citations(part, validKeys, onCite)}</span>
    )
  })
}

const cells = (row) =>
  row
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((c) => c.trim())
const isSeparator = (row) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(row)

function Table({ rows, validKeys, onCite }) {
  const [head, ...body] = rows.filter((r) => !isSeparator(r)).map(cells)
  return (
    <div className="overflow-x-auto rounded-md border border-line">
      <table className="w-full min-w-[28rem] text-left text-xs text-ink-2">
        <thead className="bg-surface-2 text-muted">
          <tr>
            {head.map((h, i) => (
              <th key={i} scope="col" className="px-2 py-1.5 font-medium">
                {inline(h, validKeys, onCite)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, r) => (
            <tr key={r} className="border-t border-line">
              {row.map((c, i) => (
                <td key={i} className="px-2 py-1.5 align-top">
                  {inline(c, validKeys, onCite)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function renderBody(text, validKeys, onCite) {
  const blocks = []
  let bullets = []
  let table = []
  const flush = () => {
    if (bullets.length) {
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="ml-5 list-disc space-y-1 text-sm text-ink-2">
          {bullets.map((b, i) => (
            <li key={i}>{inline(b, validKeys, onCite)}</li>
          ))}
        </ul>,
      )
      bullets = []
    }
    if (table.length) {
      blocks.push(<Table key={`table-${blocks.length}`} rows={table} validKeys={validKeys} onCite={onCite} />)
      table = []
    }
  }
  text.split('\n').forEach((raw, i) => {
    const line = raw.replace(/\s+$/, '')
    if (line.trimStart().startsWith('|')) {
      if (bullets.length) flush()
      table.push(line)
      return
    }
    if (/^\s*[-*] /.test(line)) {
      if (table.length) flush()
      bullets.push(line.replace(/^\s*[-*] /, ''))
      return
    }
    flush()
    if (line.startsWith('## ')) {
      blocks.push(
        <h3 key={i} className="mt-4 text-sm font-semibold text-ink first:mt-0">
          {line.slice(3)}
        </h3>,
      )
    } else if (line.startsWith('# ')) {
      blocks.push(
        <h2 key={i} className="text-base font-semibold text-ink">
          {line.slice(2)}
        </h2>,
      )
    } else if (line.trim()) {
      blocks.push(
        <p key={i} className="text-sm leading-relaxed text-ink-2">
          {inline(line, validKeys, onCite)}
        </p>,
      )
    }
  })
  flush()
  return blocks
}

/** The investigation brief with clickable [E#] citations and who wrote it. */
export default function Brief({ brief, validKeys, onCite }) {
  const fromLlm = brief.source === 'llm'
  // The badge names who really wrote it: Claude, Grok, Groq or the built-in Template.
  const label = fromLlm ? (brief.provider_label ?? 'LLM') : 'Template'
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Badge
          tone={fromLlm ? 'accent' : 'neutral'}
          title={fromLlm && brief.model ? `Model: ${brief.model}` : undefined}
          data-testid="brief-source"
        >
          Source: {label}
        </Badge>
        {!fromLlm && brief.fallback_reason && (
          <span className="text-xs text-muted">Written by the built-in template: {brief.fallback_reason}.</span>
        )}
        {fromLlm && (
          <>
            {brief.masked && (
              <span className="text-xs text-ink-2" data-testid="masked-note">
                Generated from masked data. No personal details were shared.
              </span>
            )}
            <span className="text-xs text-muted">Checked against the evidence before it is shown.</span>
          </>
        )}
      </div>
      <div className="space-y-2" data-testid="brief-body">
        {renderBody(brief.brief, validKeys, onCite)}
      </div>
    </div>
  )
}
