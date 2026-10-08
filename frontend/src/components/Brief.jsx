import { Badge } from './ui'

const CITATION = /(\[E\d+\])/g

/** Split text on [E#] markers; known keys become buttons, anything else stays plain text. */
function renderInline(text, validKeys, onCite) {
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

function renderBody(text, validKeys, onCite) {
  const blocks = []
  let bullets = []
  const flush = () => {
    if (bullets.length) {
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="ml-5 list-disc space-y-1 text-sm text-ink-2">
          {bullets.map((b, i) => (
            <li key={i}>{renderInline(b, validKeys, onCite)}</li>
          ))}
        </ul>,
      )
      bullets = []
    }
  }
  text.split('\n').forEach((line, i) => {
    if (line.startsWith('- ')) {
      bullets.push(line.slice(2))
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
          {renderInline(line, validKeys, onCite)}
        </p>,
      )
    }
  })
  flush()
  return blocks
}

/** The investigation brief with clickable [E#] citations and its source (LLM or template). */
export default function Brief({ brief, validKeys, onCite }) {
  const fromLlm = brief.source === 'llm'
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Badge tone={fromLlm ? 'accent' : 'neutral'} data-testid="brief-source">
          Source: {fromLlm ? `LLM${brief.model ? ` (${brief.model})` : ''}` : 'template'}
        </Badge>
        {!fromLlm && brief.fallback_reason && (
          <span className="text-xs text-muted">Written by the built-in template: {brief.fallback_reason}.</span>
        )}
        {fromLlm && <span className="text-xs text-muted">Checked against the evidence before it is shown.</span>}
      </div>
      <div className="space-y-2" data-testid="brief-body">
        {renderBody(brief.brief, validKeys, onCite)}
      </div>
    </div>
  )
}
