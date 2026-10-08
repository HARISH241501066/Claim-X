import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// Built in pieces so this file never contains the phrases it forbids.
const BANNED = ['fraud' + ' probability', 'chance of ' + 'fraud', 'fraud' + '_prob']
const ROOT = process.cwd() // npm test runs from the frontend folder

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    if (['node_modules', 'dist', 'screenshots'].includes(name)) return []
    const full = join(dir, name)
    return statSync(full).isDirectory() ? files(full) : [full]
  })
}

describe('wording', () => {
  const sources = [...files(join(ROOT, 'src')), ...files(join(ROOT, 'e2e')), join(ROOT, 'index.html')]

  it('scans a real set of files', () => {
    expect(sources.length).toBeGreaterThan(25)
  })

  it.each(BANNED.map((phrase) => [phrase.length]))('no source file contains a banned phrase (%i characters)', () => {
    const hits = []
    for (const file of sources) {
      const text = readFileSync(file, 'utf8').toLowerCase()
      for (const phrase of BANNED) if (text.includes(phrase)) hits.push(`${file}: ${phrase}`)
    }
    expect(hits).toEqual([])
  })

  it('the only caveat that mentions fraud says it is not a finding of fraud', () => {
    const mentions = []
    for (const file of sources.filter((f) => /src[\\/](components|pages|lib)[\\/][^\\/]+\.jsx?$/.test(f) && !/\.test\./.test(f))) {
      readFileSync(file, 'utf8')
        .split('\n')
        .forEach((line) => {
          if (/fraud/i.test(line)) mentions.push(line.trim())
        })
    }
    expect(mentions).toHaveLength(1)
    expect(mentions[0]).toContain('Not a finding of fraud.')
  })
})
