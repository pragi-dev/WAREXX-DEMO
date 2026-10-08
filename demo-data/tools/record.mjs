// Record the static demo's data: drive the real app (headless Edge/Chrome over
// the DevTools protocol) through every screen of a RUNNING server that holds the
// sample business, and save every /api, /pos and /m answer it receives.
//
//   node landing-demo/demo-data/tools/record.mjs http://127.0.0.1:8020
//
// Writes landing-demo/demo-data/recorded/manifest.json and data/bodies/*, which
// landing-demo/frontend/demo/public/sw.js replays. See landing-demo/README.md for the whole build.
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync, rmSync, existsSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { scrub } from './scrub.mjs'

const BASE = (process.argv[2] || 'http://127.0.0.1:8020').replace(/\/$/, '')
const ORIGIN = new URL(BASE).origin
const HERE = path.dirname(fileURLToPath(import.meta.url))
const OUT = path.resolve(HERE, '..', 'recorded')
const BROWSER = process.env.BROWSER || [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
].find((p) => existsSync(p))
const USER = process.env.DEMO_USER || 'superadmin'
const PASS = process.env.DEMO_PASS || 'super@123'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const log = (...a) => console.log('[record]', ...a)

// ---------- screens ----------
const WAREHOUSE_TABS = ['dashboard', 'purchase_orders', 'lr', 'documents', 'inventory', 'locator',
  'stock_audit', 'physical_audit', 'pricing', 'deadstock', 'labels', 'labelprint', 'outward', 'inward',
  'returns', 'payments', 'reports', 'suppliers', 'masters', 'catalogues', 'locations', 'audit', 'users']
const COMPANY_TABS = ['command', 'central', 'locator', 'reports', 'locations', 'audit', 'users']
const STORE_TABS = ['pos:home', 'pos:floor', 'pos:counter', 'pos:delivery', 'pos:inventory', 'pos:audits',
  'pos:checker', 'pos:customers', 'pos:invoices', 'pos:returns', 'pos:alterations', 'pos:stores',
  'pos:promotions', 'pos:staff', 'pos:reports']
// Only things that READ: rows, tabs and filter chips. Never a button that saves,
// deletes or posts — the recording must not change the business it records.
const SAFE_CLICKS = [
  '.doc-row', '.sup-row', 'table.items tbody tr', '[role="tab"]:not([aria-selected="true"])',
  '.chips button', '.chip', '.subtabs button', '.navtabs button', '.modcard',
]
const NEVER = /delete|remove|clear|post|confirm|cancel|save|reset|logout|sign out|close|discard|approve|reject|send|print|upload|import|export|recompute|refresh|mark all/i

// ---------- browser ----------
const port = 9600 + Math.floor(Math.random() * 300)
const profile = path.join(OUT, '..', '.record-profile')
rmSync(profile, { recursive: true, force: true })
const browser = spawn(BROWSER, ['--headless=new', '--disable-gpu', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, '--window-size=1440,900', 'about:blank'], { stdio: 'ignore' })
let targets
for (let i = 0; i < 200 && !targets?.length; i++) {
  try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json() } catch {}
  await sleep(250)
}
const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl)
await new Promise((r) => ws.addEventListener('open', r))
let seq = 0
const pending = new Map(), handlers = new Map()
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  else if (m.method && handlers.has(m.method)) handlers.get(m.method)(m.params)
})
const send = (method, params = {}) => new Promise((r) => { const id = ++seq; pending.set(id, r); ws.send(JSON.stringify({ id, method, params })) })
const on = (method, fn) => handlers.set(method, fn)
const evaluate = async (expression) => (await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result?.result?.value

// ---------- capture ----------
const reqs = new Map()              // requestId -> {method, url, wh, postData, status, type, ...}
const entries = new Map()           // key -> entry
let bodyBytes = 0
const ours = (u) => { try { const x = new URL(u); return x.origin === ORIGIN && /^\/(api(\/|$)|pos|m(\/|$))/.test(x.pathname) } catch { return false } }

on('Network.requestWillBeSent', (p) => {
  if (p.redirectResponse && reqs.has(p.requestId)) {
    // a redirect: keep the 3xx itself, so a replayed /pos POST lands the same way
    const r = reqs.get(p.requestId)
    store(r, { status: p.redirectResponse.status, type: 'text/html', location: p.redirectResponse.headers.Location || p.redirectResponse.headers.location, body: '' })
  }
  if (!ours(p.request.url)) return
  const h = p.request.headers || {}
  reqs.set(p.requestId, { method: p.request.method, url: p.request.url,
    wh: h['X-Essa-Warehouse'] || h['x-essa-warehouse'] || '-', postData: p.request.postData })
})
on('Network.responseReceived', (p) => {
  const r = reqs.get(p.requestId); if (!r) return
  const h = p.response.headers || {}
  r.status = p.response.status
  r.type = h['content-type'] || h['Content-Type'] || p.response.mimeType
  r.disposition = h['content-disposition'] || h['Content-Disposition']
})
on('Network.loadingFinished', async (p) => {
  const r = reqs.get(p.requestId); if (!r || r.status == null) return
  const res = await send('Network.getResponseBody', { requestId: p.requestId })
  if (!res.result) return
  store(r, { status: r.status, type: r.type, disposition: r.disposition,
    body: res.result.body, base64: res.result.base64Encoded })
})
on('Page.javascriptDialogOpening', () => send('Page.handleJavaScriptDialog', { accept: false }))

function store(r, x) {
  const u = new URL(r.url)
  const method = r.method.toUpperCase()
  if (method !== 'GET' && !/^\/api\/(auth\/login|reports|command\/overview)/.test(u.pathname)) return
  if (x.status >= 500) return
  // scrubbed like the bodies: a sign-in token in a query string is a credential
  const key = scrub(`${method} ${r.wh} ${u.pathname}${u.search}`)
  const e = { key, method, wh: r.wh, path: u.pathname, search: scrub(u.search), status: x.status,
    type: x.type || 'application/json' }
  if (x.disposition) e.disposition = x.disposition
  if (x.location) e.location = x.location.replace(ORIGIN, '')
  const textual = !x.base64 && /json|text|javascript|css|svg|xml|html/.test(e.type)
  if (textual && x.body.length < 150_000) {
    e.body = scrub(x.body.split(ORIGIN).join(''))  // relative links; placeholder names only
  } else {
    const buf = x.base64 ? Buffer.from(x.body, 'base64') : Buffer.from(scrub(x.body.split(ORIGIN).join('')))
    const name = createHash('sha1').update(buf).digest('hex').slice(0, 16) + ext(e.type)
    writeFileSync(path.join(OUT, 'bodies', name), buf)
    e.file = name
    bodyBytes += buf.length
  }
  entries.set(key, e)
}
const ext = (t = '') => t.includes('json') ? '.json' : t.includes('html') ? '.html' : t.includes('png') ? '.png'
  : t.includes('jpeg') ? '.jpg' : t.includes('pdf') ? '.pdf' : t.includes('css') ? '.css'
  : t.includes('javascript') ? '.js' : t.includes('svg') ? '.svg' : '.bin'

// ---------- tour ----------
rmSync(OUT, { recursive: true, force: true })
mkdirSync(path.join(OUT, 'bodies'), { recursive: true })
await send('Network.enable', { maxTotalBufferSize: 200_000_000, maxResourceBufferSize: 50_000_000 })
await send('Page.enable')
await send('Network.setCacheDisabled', { cacheDisabled: true })
await send('Page.navigate', { url: BASE + '/' }); await sleep(3000)

const login = await evaluate(`(async () => {
  const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: ${JSON.stringify(USER)}, password: ${JSON.stringify(PASS)} }) })
  const j = await r.json(); localStorage.setItem('essa_token', j.token); localStorage.setItem('wrx_entered', '1')
  const w = await fetch('/api/locations/warehouses', { headers: { 'X-Essa-Token': j.token } }).then(r => r.json())
  return JSON.stringify({ token: j.token, warehouses: w.map(x => ({ id: x.id, name: x.name, code: x.code, catalogue: x.catalogue })) })
})()`)
const { token, warehouses } = JSON.parse(login)
log('signed in;', warehouses.length, 'warehouses')
// The Store/POS screens are the shop app inside a frame, with a sign-in of its
// own (a Flask session cookie on the same origin). Sign it in once, so every
// /pos page is recorded with its data rather than as the shop's login form.
const shop = await evaluate(`(async () => {
  const r = await fetch('/pos/login', { method: 'POST', redirect: 'follow',
    body: new URLSearchParams({ username: ${JSON.stringify(process.env.SHOP_USER || 'admin')},
                                password: ${JSON.stringify(process.env.SHOP_PASS || 'admin123')} }) })
  return r.url
})()`)
log('shop signed in →', shop)

async function visit(tab, wh) {
  await evaluate(`(() => {
    localStorage.setItem('essa_tab', ${JSON.stringify(tab)});
    ${wh ? `localStorage.setItem('essa_warehouse', ${JSON.stringify(JSON.stringify(wh))})` : "localStorage.removeItem('essa_warehouse')"};
    localStorage.removeItem('essa_open_tabs');
  })()`)
  await send('Page.navigate', { url: BASE + '/' })
  await sleep(+process.env.WAIT || 2500)
  await explore(tab)
}

async function explore(tab) {
  // click through the safe things on screen, one at a time, letting each load
  for (const sel of SAFE_CLICKS) {
    const n = await evaluate(`document.querySelectorAll(${JSON.stringify(sel)}).length`) || 0
    const limit = tab === 'reports' && sel === '.doc-row' ? 200 : sel.includes('tr') ? 3 : 8
    for (let i = 0; i < Math.min(n, limit); i++) {
      const clicked = await evaluate(`(() => {
        const el = document.querySelectorAll(${JSON.stringify(sel)})[${i}]
        if (!el || ${NEVER}.test(el.textContent || '') || el.closest('.modal')) return false
        el.scrollIntoView({ block: 'center' }); el.click(); return true
      })()`)
      if (clicked) await sleep(650)
    }
  }
  // reports: every report in the list
  if (tab === 'reports') {
    const n = await evaluate(`document.querySelectorAll('.sidebar .list button, .sidebar .list [role="button"], .rptlist button').length`) || 0
    for (let i = 0; i < n; i++) {
      await evaluate(`document.querySelectorAll('.sidebar .list button, .sidebar .list [role="button"], .rptlist button')[${i}]?.click()`)
      await sleep(700)
    }
  }
}

for (const tab of COMPANY_TABS) { log('company', tab); await visit(tab, null) }
for (const wh of warehouses) {
  for (const tab of WAREHOUSE_TABS) { log(wh.code, tab); await visit(tab, wh) }
  for (const tab of STORE_TABS) { log(wh.code, tab); await visit(tab, wh) }
}

// ---------- detail crawl: every record a list showed, not only the ones clicked ----------
const shapeOf = (p) => p.replace(/\/\d+(?=\/|$)/g, '/:n')
const detailShapes = new Map()           // collection path -> Set(shape suffix)
for (const e of entries.values()) {
  if (e.method !== 'GET') continue
  const m = e.path.match(/^(\/api\/[a-z0-9_\-/]+?)\/\d+(\/.*)?$/)
  if (m) { if (!detailShapes.has(m[1])) detailShapes.set(m[1], new Set()); detailShapes.get(m[1]).add(m[2] || '') }
}
const idsFor = new Map()                 // collection -> Map(wh -> Set(id))
for (const e of entries.values()) {
  if (e.method !== 'GET' || !detailShapes.has(e.path) || !e.body) continue
  let j; try { j = JSON.parse(e.body) } catch { continue }
  const rows = Array.isArray(j) ? j : (j.items || j.rows || j.results || j.data || [])
  if (!Array.isArray(rows)) continue
  if (!idsFor.has(e.path)) idsFor.set(e.path, new Map())
  const byWh = idsFor.get(e.path)
  if (!byWh.has(e.wh)) byWh.set(e.wh, new Set())
  for (const r of rows) if (r && (typeof r.id === 'number')) byWh.get(e.wh).add(r.id)
}
let crawled = 0
for (const [col, byWh] of idsFor) {
  for (const [wh, ids] of byWh) {
    for (const id of [...ids].slice(0, 80)) {
      for (const suffix of detailShapes.get(col)) {
        const p = `${col}/${id}${suffix}`
        if (entries.has(`GET ${wh} ${p}`)) continue
        await evaluate(`fetch(${JSON.stringify(p)}, { headers: { 'X-Essa-Token': ${JSON.stringify(token)} ${wh !== '-' ? `, 'X-Essa-Warehouse': ${JSON.stringify(wh)}` : ''} } }).then(r => r.arrayBuffer()).catch(() => null)`)
        crawled++
      }
    }
  }
}
await sleep(2500)
log('detail crawl:', crawled, 'requests')

// ---------- write ----------
const list = [...entries.values()].sort((a, b) => a.key.localeCompare(b.key))
writeFileSync(path.join(OUT, 'manifest.json'), JSON.stringify({
  meta: { recordedAt: new Date().toISOString(), source: BASE, warehouses,
          writeMessage: 'Saved in the demo — the sample data resets when you reload.' },
  entries: list }))
log(`${list.length} answers recorded, ${(bodyBytes / 1e6).toFixed(1)} MB in files`)
ws.close(); browser.kill(); rmSync(profile, { recursive: true, force: true })
process.exit(0)
