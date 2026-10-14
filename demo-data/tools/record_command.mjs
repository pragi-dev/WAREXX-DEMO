// Add the Command Center's overview to an existing recording, in place.
//
// The Command Center (company → Command Center) reads GET /api/command/overview.
// The recording of 2026-10-05 was made before that screen existed, so the demo
// answered it with an empty list and the screen said "The stores' till database
// is not readable from here" with Sales, Discounts, Profit and Store stock blank.
//
// This asks the REAL backend, serving the SAME scratch databases the recording
// was made from (demo-build/demo.db + shop.db), for the overview — the whole
// company and each warehouse, for the recording's last day and the six before
// it — and adds the answers to recorded/manifest.json, scrubbed like every other
// answer. Nothing is written by hand: the figures are the server's.
//
//   1. serve the scratch databases (the till too — ESSA_POS_DB):
//        ESSA_POS_DB=<demo-build>/shop.db DATABASE_URL=sqlite:///<demo-build>/shop.db \
//        ESSA_DATABASE_URL=sqlite:///<demo-build>/demo.db ESSA_WAREHOUSE_DB=<demo-build>/demo.db \
//        ESSA_STATE_DIR=<demo-build> app/backend/.venv/Scripts/python -m uvicorn app.main:app --port 8020
//   2. node demo-data/tools/record_command.mjs [http://127.0.0.1:8020] [--day 2026-10-05]
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { scrub } from './scrub.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FILE = path.resolve(HERE, '..', 'recorded', 'manifest.json')
const args = process.argv.slice(2)
const BASE = (args.find((a) => /^https?:/.test(a)) || 'http://127.0.0.1:8020').replace(/\/$/, '')
const m = JSON.parse(readFileSync(FILE, 'utf8'))
// the recording's "today": the day it was made, unless told otherwise
const LAST = args.includes('--day') ? args[args.indexOf('--day') + 1] : String(m.meta.recordedAt).slice(0, 10)
const DAYS = 7
const log = (...a) => console.log('[record_command]', ...a)

const login = await (await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: process.env.DEMO_USER || 'superadmin', password: process.env.DEMO_PASS || 'super@123' }) })).json()
if (!login.token) throw new Error('could not sign in to the recording server')
const auth = { Authorization: 'Bearer ' + login.token }

const days = Array.from({ length: DAYS }, (_, i) => {
  const d = new Date(LAST + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - i); return d.toISOString().slice(0, 10)
})
const scopes = [undefined, ...m.meta.warehouses.map((w) => w.id)]
const fresh = []
// the default (no day: "today", the recording's last day) first — the demo
// replays the first close match for a request without a day
for (const day of [undefined, ...days]) {
  for (const wid of scopes) {
    const q = new URLSearchParams()
    if (day) q.set('day', day)
    if (wid) q.set('warehouse_id', String(wid))
    const asked = new URLSearchParams(q); if (!day) asked.set('day', LAST)
    const r = await fetch(`${BASE}/api/command/overview?${asked}`, { headers: auth })
    const body = await r.text()
    if (!r.ok) throw new Error(`overview ${asked}: HTTP ${r.status}`)
    const j = JSON.parse(body)
    if (!j.pos || !j.pos.available) throw new Error(`overview ${asked}: the till is not available — set ESSA_POS_DB`)
    const search = q.toString() ? '?' + q.toString() : ''
    fresh.push({ key: scrub(`GET - /api/command/overview${search}`), method: 'GET', wh: '-', path: '/api/command/overview',
      search: scrub(search), status: 200, type: 'application/json', body: scrub(body) })
    log(`${day || 'today (' + LAST + ')'} ${wid ? 'warehouse ' + wid : 'company'}: sales ${j.kpis?.sales?.value}, bills ${j.kpis?.sales?.bills}`)
  }
}
const keep = m.entries.filter((e) => e.path !== '/api/command/overview')
m.entries = [...keep, ...fresh]
m.meta.command = { recordedAt: new Date().toISOString(), lastDay: LAST, entries: fresh.length }
writeFileSync(FILE, JSON.stringify(m))
log(`${fresh.length} answers added (${keep.length} others kept)`)
