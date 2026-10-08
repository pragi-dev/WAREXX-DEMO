// Names that reach the demo from the APP'S OWN shipped masters rather than from
// the seeders — the category master ("ESSA MENS-T-SHIRT"), the default product
// code prefix (ESSA-00048) and the brand/design option list. The demo shows only
// placeholder data, so record.mjs passes every recorded text answer through this.
//
// It also removes the recording server's sign-in tokens (user.expiry.signature):
// such a token is a working credential on any server that shares the recording
// server's secret, and the demo needs none — sw.js signs every visitor in as
// "demo" itself.
//
//   node demo-data/tools/scrub.mjs     re-applies it to demo-data/recorded in place
import { readFileSync, writeFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

// case-sensitive on purpose: the shop's own UI text ("inside the Essa backend")
// is the app's wording, not data, and is left alone
const RULES = [
  [/\bESSA-(?=[A-Z0-9])/g, 'SMPL-'],          // product codes, carton codes (ESSA-B-…)
  [/\bESSA (?=[A-Z])/g, 'HOUSE '],
  [/\bTAQUA-(\d)/g, 'DSN-$1'],
  [/\bTAQUA\b/g, 'HOUSE'],
  [/Taqua Casual/g, 'Sample Casual'],
  [/Essa Show Room/g, 'Sample Showroom'],
  [/\b[a-z0-9_]+\.\d{9,11}\.[0-9a-f]{64}\b/g, 'demo'],   // signed session tokens
]
export const scrub = (text) => RULES.reduce((t, [rx, to]) => t.replace(rx, to), text)

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const DATA = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'recorded')
  const mf = path.join(DATA, 'manifest.json')
  const m = JSON.parse(readFileSync(mf, 'utf8'))
  let n = 0
  for (const e of m.entries) {
    let hit = false
    for (const k of ['body', 'key', 'search', 'path', 'location']) {
      if (typeof e[k] !== 'string') continue
      const s = scrub(e[k]); if (s !== e[k]) { e[k] = s; hit = true }
    }
    if (hit) n++
  }
  writeFileSync(mf, JSON.stringify(m))
  let f = 0
  for (const name of readdirSync(path.join(DATA, 'bodies'))) {
    if (!/\.(json|html|js|css|txt|svg)$/.test(name)) continue
    const p = path.join(DATA, 'bodies', name)
    const t = readFileSync(p, 'utf8'); const s = scrub(t)
    if (s !== t) { writeFileSync(p, s); f++ }
  }
  console.log(`scrubbed ${n} recorded answers and ${f} body files`)
}
