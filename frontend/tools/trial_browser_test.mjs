// The booked-slot demo, end to end in a real (headless) browser:
//   landing contact form with a slot → demo sign-up (email filled in) → the demo
//   → the window ends → the subscribe card; and the password refused afterwards.
//
//   python ../lead-api/server.py        with DEMO_TRIAL_MINUTES=1 (a one-minute window)
//   npm run build:site && npm run preview:site
//   node tools/trial_browser_test.mjs [http://localhost:8030]
import { spawn } from 'node:child_process'
import { existsSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const BASE = (process.argv[2] || 'http://localhost:8030').replace(/\/$/, '')
const WINDOW_S = +(process.env.WINDOW_SECONDS || 60)
const BROWSER = process.env.BROWSER || ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome', '/usr/bin/chromium'].find((p) => existsSync(p))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
let failed = 0
const ok = (c, what, extra = '') => { if (c) console.log('  ok    ' + what); else { failed++; console.log('  FAIL  ' + what + (extra ? '\n        ' + extra : '')) } }

const port = 9400 + Math.floor(Math.random() * 200)
const profile = path.join(os.tmpdir(), `wx-demo-test-trial-${port}`)
const browser = spawn(BROWSER, ['--headless=new', '--disable-gpu', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, '--window-size=1440,900', 'about:blank'], { stdio: 'ignore' })
let t
for (let i = 0; i < 120 && !t; i++) {
  try { t = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((x) => x.type === 'page') } catch {}
  await sleep(250)
}
const ws = new WebSocket(t.webSocketDebuggerUrl)
await new Promise((r) => ws.addEventListener('open', r))
let id = 0
const pend = new Map()
ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); if (m.id) { pend.get(m.id)?.(m); pend.delete(m.id) } })
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method, params })) })
const ev = async (x) => { const r = await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true })
  return r.result?.result?.value ?? ('EXC ' + (r.result?.exceptionDetails?.exception?.description || '')) }
const until = async (x, ms = 20000) => { for (let i = 0; i < ms; i += 400) { if (await ev(x)) return true; await sleep(400) } return false }
await send('Page.enable'); await send('Runtime.enable')
const SHOTS = process.env.SHOTS || ''
const shot = async (name) => { if (!SHOTS) return; const { mkdirSync, writeFileSync } = await import('node:fs')
  mkdirSync(SHOTS, { recursive: true }); const r = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(path.join(SHOTS, name + '.png'), Buffer.from(r.result.data, 'base64')) }

console.log(`booked-slot demo (${BASE})`)
await send('Page.navigate', { url: BASE + '/' })
await until(`!!document.querySelector('form[data-type=contact] select[data-slots] option[value=now]')`)
ok(await ev(`document.querySelectorAll('form[data-type=contact] select[data-slots] option').length > 2`),
  'the contact form offers "Start now" and upcoming slots')
const EMAIL = `trial-${Date.now()}@example.com`
await ev(`(() => { const f = document.querySelector('form[data-type=contact]')
  const set = (n, v) => { const el = f.querySelector('[name=' + n + ']'); el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) }
  set('name', 'Trial Tester'); set('email', ${JSON.stringify(EMAIL)}); set('company', 'Example Traders'); set('phone', '9000000000')
  const ind = f.querySelector('[name=industry]'); ind.value = ind.options[1].value
  const intr = f.querySelector('[name=interest]'); intr.value = intr.options[1].value
  f.querySelector('[name=slot]').value = 'now'
  f.querySelector('button[type=submit]').click(); return true })()`)
ok(await until(`location.pathname.startsWith('/demo') && !!document.querySelector('.wxtrial-form')`, 25000),
  'sending the form opens the demo sign-up')
ok(await ev(`document.querySelector('.wxtrial-form input[type=email]').value`) === EMAIL, 'the email is filled in from the form')
ok(await ev(`document.querySelector('.wxtrial-form input[type=email]').readOnly`), '…and cannot be changed')
ok(!(await ev(`location.hash`)), 'the sign-up pass is taken out of the address bar')
ok(/Create your demo account/.test(await ev('document.body.innerText')), 'it is the sign-up step (choose a password)')
await shot('1-signup')

const type = async (sel, v) => ev(`(() => { const el = document.querySelector(${JSON.stringify(sel)})
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
  set.call(el, ${JSON.stringify(v)}); el.dispatchEvent(new Event('input', { bubbles: true })); return true })()`)
const pws = '.wxtrial-form input[type=password]'
await ev(`(() => { const p = document.querySelectorAll(${JSON.stringify(pws)})
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
  for (const el of p) { set.call(el, 'demo-pass-123'); el.dispatchEvent(new Event('input', { bubbles: true })) }
  document.querySelector('.wxtrial-form button[type=submit]').click(); return true })()`)
const startedAt = Date.now()
ok(await until(`!!document.querySelector('.wx-wsseg') && !document.querySelector('.wxtrial-form')`, 30000),
  'after choosing a password the WAREXX demo opens')
ok(/Demo time left/.test(await ev('document.body.innerText')), 'a countdown shows the time left')
await sleep(1500); await shot('2-demo')

await send('Page.reload')
ok(await until(`!!document.querySelector('.wx-wsseg')`, 30000), 'a reload inside the window goes straight back in (no password)')

console.log(`  …   waiting for the ${WINDOW_S}s window to end`)
const rest = WINDOW_S * 1000 - (Date.now() - startedAt) + 3000
if (rest > 0) await sleep(rest)
ok(await until(`!!document.querySelector('.wxtrial-overlay')`, 8000), 'when the time is up a card covers the demo')
ok(/Subscribe to WAREXX/.test(await ev('document.body.innerText')), '…offering to subscribe')
await shot('3-ended')

await send('Page.reload')
await until(`!!document.querySelector('.wxtrial-card')`, 20000)
const body = await ev('document.body.innerText')
ok(!(await ev(`!!document.querySelector('.wx-wsseg')`)), 'after a reload the demo does not open')
ok(/Sign in to your demo/.test(body), '…it asks for the password again')
await type(pws, 'demo-pass-123')
await ev(`document.querySelector('.wxtrial-form button[type=submit]').click(), true`)
ok(await until(`/Your demo time is over/.test(document.body.innerText)`, 15000),
  'the right password no longer works after the window: the subscribe card instead')

ws.close(); browser.kill()
await sleep(1500)
try { rmSync(profile, { recursive: true, force: true }) } catch {}
console.log(failed ? `\n${failed} FAILED` : '\nall passing')
process.exit(failed ? 1 : 0)
