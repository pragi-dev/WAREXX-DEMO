// The interactive demo, in a real (headless) browser: it starts, shows the
// sample business on several screens, answers Ask WAREXX, accepts a save without
// sending it anywhere, and cannot reach another host.
//
//     npm run preview:demo                       (in one window)
//     node tools/demo_browser_test.mjs [url]     (default http://localhost:8002/)
//
// Optional: PROD_URL=http://127.0.0.1:8000 to prove a request to a running
// production server is refused by the browser; SHOTS=<folder> for screenshots.
// Uses Edge or Chrome (BROWSER=<path> to choose), as demo-data/tools/record.mjs does.
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const BASE = (process.argv[2] || 'http://localhost:8002/').replace(/\/?$/, '/')
const ORIGIN = new URL(BASE).origin
const PROD = process.env.PROD_URL || ''
const SHOTS = process.env.SHOTS || ''
const BROWSER = process.env.BROWSER || [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find((p) => existsSync(p))
if (!BROWSER) { console.error('No Edge/Chrome found (set BROWSER)'); process.exit(2) }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let failed = 0
const ok = (cond, what, extra = '') => {
  if (cond) console.log('  ok    ' + what)
  else { failed++; console.log('  FAIL  ' + what + (extra ? '\n        ' + extra : '')) }
}

const port = 9300 + Math.floor(Math.random() * 300)
const profile = path.join(os.tmpdir(), `wx-demo-test-${port}`)
rmSync(profile, { recursive: true, force: true })
const browser = spawn(BROWSER, ['--headless=new', '--disable-gpu', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, '--window-size=1440,900', 'about:blank'], { stdio: 'ignore' })
const stop = () => { try { browser.kill() } catch {} }
process.on('exit', stop)

let targets
for (let i = 0; i < 120 && !targets?.length; i++) {
  try { targets = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).filter((t) => t.type === 'page') } catch {}
  await sleep(250)
}
const ws = new WebSocket(targets[0].webSocketDebuggerUrl)
await new Promise((r) => ws.addEventListener('open', r))
let seq = 0
const pending = new Map(), handlers = []
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  else if (m.method) for (const [name, fn] of handlers) if (name === m.method) fn(m.params)
})
const send = (method, params = {}) => new Promise((r) => { const id = ++seq; pending.set(id, r); ws.send(JSON.stringify({ id, method, params })) })
const evaluate = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  return r.result?.result?.value ?? r.result?.exceptionDetails?.exception?.description
}
const shot = async (name) => {
  if (!SHOTS) return
  mkdirSync(SHOTS, { recursive: true })
  const r = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(path.join(SHOTS, name + '.png'), Buffer.from(r.result.data, 'base64'))
}

// every request the page (or its worker) sends to the network
const requests = []
handlers.push(['Network.requestWillBeSent', (p) => requests.push({ method: p.request.method, url: p.request.url })])
const cspBlocked = []
handlers.push(['Log.entryAdded', (p) => { if (/Content Security Policy/i.test(p.entry.text)) cspBlocked.push(p.entry.text) }])
handlers.push(['Runtime.consoleAPICalled', (p) => {
  const t = (p.args || []).map((a) => a.value ?? a.description ?? '').join(' ')
  if (/Content Security Policy/i.test(t)) cspBlocked.push(t)
}])
await send('Network.enable'); await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable')
await send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true })

const open = async (url, waitFor, ms = 20000) => {
  await send('Page.navigate', { url })
  for (let t = 0; t < ms; t += 500) {
    await sleep(500)
    if (await evaluate(waitFor)) return true
  }
  return false
}
const text = () => evaluate('document.body.innerText')

console.log(`demo in a browser (${BASE})`)
const started = await open(BASE, `!document.getElementById('boot') && document.getElementById('root')?.innerText.length > 200`, 30000)
const t0 = await text()
ok(started && !/could not start/i.test(t0), 'the demo starts (service worker in charge, app rendered)', started ? '' : t0.slice(0, 200))
ok(await evaluate('!!navigator.serviceWorker.controller'), 'its stand-in server (sw.js) answers the page')
ok(/Sample Warehouse A/.test(t0), 'it opens inside the sample business (Sample Warehouse A)')
ok(/Live demo/.test(t0) && /Start over/.test(t0), 'the demo banner shows, with "Start over"')
await shot('01-dashboard')

for (const tab of ['inventory', 'purchase_orders', 'documents', 'reports', 'outward']) {
  await evaluate(`localStorage.setItem('essa_tab', ${JSON.stringify(tab)}); true`)
  const shown = await open(BASE, `document.getElementById('root')?.innerText.length > 200 && !document.getElementById('boot')`)
  const body = await text()
  ok(shown && body.length > 300 && !/could not start/i.test(body), `screen "${tab}" renders (${body.length} chars of content)`)
  await shot('02-' + tab)
}

// Ask WAREXX through the page's own fetch → the worker → the demo dataset
const askJs = (q) => `fetch('/api/assist/command', { method: 'POST', headers: { 'Content-Type': 'application/json',
  'X-Essa-Warehouse': '1' }, body: JSON.stringify({ text: ${JSON.stringify(q)}, source: 'text' }) })
  .then(r => r.json()).then(j => (j.intent && j.intent.intent) + ' | ' + (j.line || j.title || ''))`
for (const q of ['How much stock do we have?', 'Which products are low in stock?', 'Which purchase orders are overdue?']) {
  const a = await evaluate(askJs(q))
  ok(typeof a === 'string' && !/^NONE|^undefined/.test(a), `Ask WAREXX "${q}" → ${String(a).slice(0, 90)}`)
}

// a save: acknowledged by the worker, never sent
const before = requests.length
const save = await evaluate(`fetch('/api/purchase-orders', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ supplier_name: 'Browser test', lines: [] }) }).then(r => r.status + ' ' + r.headers.get('X-Warexx-Demo'))`)
await sleep(500)
const sentWrites = requests.slice(before).filter((r) => r.method !== 'GET')
ok(save === '200 1', `a save (POST /api/purchase-orders) is answered by the demo itself (${save})`)
ok(sentWrites.every((r) => !/^https?:/.test(r.url) || new URL(r.url).origin === ORIGIN),
  'no write left the demo origin', JSON.stringify(sentWrites))

// another host — the production API — is refused by the browser
if (PROD) {
  const r = await evaluate(`fetch(${JSON.stringify(PROD.replace(/\/$/, '') + '/api/status')}).then(r => 'REACHED ' + r.status).catch(e => 'BLOCKED ' + e.name)`)
  const w = await evaluate(`fetch(${JSON.stringify(PROD.replace(/\/$/, '') + '/api/purchase-orders')}, { method: 'POST', body: '{}' }).then(r => 'REACHED ' + r.status).catch(e => 'BLOCKED ' + e.name)`)
  await sleep(500)
  ok(/^BLOCKED/.test(r), `a read from the production server ${PROD} is refused (${r})`)
  ok(/^BLOCKED/.test(w), `a write to the production server is refused (${w})`)
  ok(cspBlocked.length > 0, `…by the Content-Security-Policy (${cspBlocked.length} violation(s) reported)`)
}

// the phone app
const phone = await open(BASE + 'm/', `document.body && document.body.innerText.length > 50`)
ok(phone, 'the phone app (/m) opens')
await shot('03-phone')

const hosts = [...new Set(requests.map((r) => { try { return new URL(r.url).origin } catch { return r.url.slice(0, 20) } }))]
const allowed = (h) => h === ORIGIN || /fonts\.(googleapis|gstatic)\.com$/.test(new URL(h).hostname) || /^(data|blob|chrome|null)/.test(h)
const strays = hosts.filter((h) => { try { return !allowed(h) } catch { return false } })
  .filter((h) => !PROD || h !== new URL(PROD).origin)        // the attempts above, which were refused
ok(strays.length === 0, `every request went to the demo origin (or Google Fonts): ${hosts.join(', ')}`, strays.join(', '))

ws.close(); stop()
await sleep(1500)                    // the browser lets go of its profile
try { rmSync(profile, { recursive: true, force: true }) } catch { /* removed by the OS later */ }
console.log(failed ? `\n${failed} FAILED` : '\nall passing')
process.exit(failed ? 1 : 0)
