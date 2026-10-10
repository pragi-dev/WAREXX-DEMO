// The public marketing site (landing/index.html): one hand-written,
// self-contained page — no framework, no authentication, no WAREXX backend or
// database. It is deliberately NOT run through Vite: the page is shipped exactly
// as written, with only its three addresses filled in.
//
//   node tools/landing.mjs build     → dist/landing/  (static files, any host)
//   node tools/landing.mjs dev       → http://localhost:5174
//
// The three addresses (WAREXX_CONFIG in the page) come from VITE_LEAD_ENDPOINT,
// VITE_DEMO_URL and VITE_APP_URL — in the environment, or in landing-demo/.env.
// They end up in the page, so they are public by nature; only VITE_* names are
// read, never a secret.
//
// The page's only server call is the lead form, POSTed to VITE_LEAD_ENDPOINT —
// by default "/api/leads", the landing site's own lead API (../api/leads.py on
// Vercel, ../lead-api/server.py anywhere else), which sends email and nothing
// more. `dev` forwards /api/leads to that API on LEAD_API_URL (default :8003).
import { spawnSync } from 'node:child_process'
import { createServer, request as httpRequest } from 'node:http'
import { cpSync, createReadStream, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const SRC = path.join(FRONTEND, 'landing', 'index.html')
const OUT = path.join(FRONTEND, 'dist', 'landing')
// Files the page loads that are not inlined in it — today only the demo film's
// recorded video, which is too large for git (see tools/import-landing.mjs).
const ASSETS = path.join(FRONTEND, 'landing', 'assets')
const LOCAL_VIDEO = 'assets/warexx-demo.mp4'

/** Where the demo film's video comes from: VITE_DEMO_VIDEO_URL when set (a
 *  hosted copy), else the local file when it is present, else "" — and with
 *  "" the page plays its built-in animated film rather than a broken player. */
export function demoVideoUrl() {
  const fromFile = fileEnv()
  const set = process.env.VITE_DEMO_VIDEO_URL ?? fromFile.VITE_DEMO_VIDEO_URL
  if (set !== undefined) return set
  return existsSync(path.join(FRONTEND, 'landing', LOCAL_VIDEO)) ? LOCAL_VIDEO : ''
}

// ---- prices in the visitor's currency (landing/extras/local-pricing.js) ------
// Rupee → currency rates, fetched once per build from open.er-api.com (free, no
// key) and written into the page between its /*RATES*/ markers. A build that
// cannot reach it keeps the rates already in the page — prices still convert,
// at the last known rates. WRX_RATES=off skips the fetch.
let ratesCache
export function liveRates() {
  if (ratesCache !== undefined) return ratesCache
  ratesCache = null
  if ((process.env.WRX_RATES || '').toLowerCase() === 'off') return null
  const r = spawnSync(process.execPath, ['-e',
    "fetch('https://open.er-api.com/v6/latest/INR',{signal:AbortSignal.timeout(8000)}).then(r=>r.json())"
    + ".then(j=>{if(j.result!=='success')process.exit(1);process.stdout.write(JSON.stringify(j.rates))}).catch(()=>process.exit(1))"],
    { encoding: 'utf8', timeout: 12000 })
  try { if (r.status === 0) ratesCache = JSON.parse(r.stdout) } catch { /* keep the page's own */ }
  if (!ratesCache) console.warn('[landing] exchange rates not reachable — keeping the rates already in the page')
  return ratesCache
}
function withRates(html) {
  const live = liveRates()
  if (!live) return html
  return html.replace(/\/\*RATES\*\/(\{[^}]*\})\/\*\/RATES\*\//, (whole, current) => {
    let mine; try { mine = JSON.parse(current) } catch { return whole }
    // only the currencies the page supports, at today's rate
    const next = {}
    for (const c of Object.keys(mine)) next[c] = live[c] ? +Number(live[c]).toPrecision(5) : mine[c]
    return `/*RATES*/${JSON.stringify(next)}/*/RATES*/`
  })
}

// ---- /pricing: the landing page with <main> holding its pricing section and
// landing/extras/pricing-page.html. Same head, header, footer, forms and
// scripts, so it looks and behaves exactly like the page it came from.
const PRICING_TPL = path.join(FRONTEND, 'landing', 'extras', 'pricing-page.html')
export function pricingPage(landingHtml) {
  const open = '<main id="main">'
  const a = landingHtml.indexOf(open), b = landingHtml.indexOf('</main>')
  const section = /<section class="screen pricing" id="pricing">[\s\S]*?<\/section>/.exec(landingHtml)
  if (a < 0 || b < 0 || !section) throw new Error('landing/index.html: no <main> or pricing section to build /pricing from')
  const body = readFileSync(PRICING_TPL, 'utf8').replace('<!--PRICING_SECTION-->', () => section[0])
  let html = landingHtml.slice(0, a + open.length) + '\n' + body + '\n' + landingHtml.slice(b)
  html = html.replace(/<html([^>]*)>/i, '<html$1 data-page="pricing">')
  html = html.replace(/<title>[^<]*<\/title>/i, '<title>Pricing — WAREXX</title>')
  // the landing page's section links lead back to it; the ones on this page stay
  return html.replace(/href="#(?!(?:top|main|pricing|pricing-details)")([A-Za-z][\w-]*)"/g, 'href="/#$1"')
}

const DEFAULTS = {
  dev: { VITE_LEAD_ENDPOINT: '/api/leads', VITE_DEMO_URL: 'http://localhost:8002/', VITE_APP_URL: 'http://localhost:8000/', VITE_PAYMENT_URL: '' },
  // "/demo/": the demo on this same site (npm run build:site). Set VITE_DEMO_URL
  // to https://demo.warexx.aavoraa.com/ once the demo has a domain of its own.
  // VITE_PAYMENT_URL: where "Subscribe" goes; empty until the payment page exists
  build: { VITE_LEAD_ENDPOINT: '/api/leads', VITE_DEMO_URL: '/demo/', VITE_APP_URL: 'https://app.warexx.aavoraa.com/', VITE_PAYMENT_URL: '' },
}
const KEYS = { VITE_LEAD_ENDPOINT: 'formEndpoint', VITE_DEMO_URL: 'demoAppUrl', VITE_APP_URL: 'appUrl', VITE_PAYMENT_URL: 'paymentUrl' }

/** VITE_* values from landing-demo/.env (KEY=VALUE lines), under the real environment. */
function fileEnv() {
  const f = path.resolve(FRONTEND, '..', '.env')
  const out = {}
  if (!existsSync(f)) return out
  for (const line of readFileSync(f, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*(VITE_[A-Z0-9_]+)\s*=\s*(.*?)\s*$/)
    if (m) out[m[1]] = m[2].replace(/^(['"])(.*)\1$/, '$2')
  }
  return out
}

export function landingConfig(mode) {
  const fromFile = fileEnv()
  const cfg = {}
  for (const [name, key] of Object.entries(KEYS)) {
    cfg[key] = process.env[name] ?? fromFile[name] ?? DEFAULTS[mode][name]
  }
  return cfg
}

/** The browser-enforced boundary: the page may send data to its own origin and
 *  to the lead endpoint's, and nowhere else — not the WAREXX API, whatever is
 *  ever added to the page. */
export function landingCsp(cfg) {
  const connect = ["'self'"]
  try {
    const lead = new URL(cfg.formEndpoint, 'https://self.invalid/')
    if (lead.origin !== 'https://self.invalid') connect.push(lead.origin)
  } catch { /* empty or relative: same origin */ }
  return `connect-src ${connect.join(' ')}; form-action 'self'; base-uri 'self'`
}

/** The page, with WAREXX_CONFIG's addresses and the policy set. Nothing else is touched. */
export function landingPage(mode) {
  let html = readFileSync(SRC, 'utf8')
  const cfg = landingConfig(mode)
  for (const [key, value] of Object.entries(cfg)) {
    const re = new RegExp(`(\\n  ${key}: )"[^"\\n]*"`)
    if (!re.test(html)) throw new Error(`landing/index.html: WAREXX_CONFIG.${key} not found`)
    html = html.replace(re, (_, lead) => lead + JSON.stringify(value))
  }
  // the film's video (optional; the page has its own film without one)
  const video = /(\n  demoVideo: \{ src: )"[^"\n]*"/
  if (video.test(html)) html = html.replace(video, (_, lead) => lead + JSON.stringify(demoVideoUrl()))
  // "Sign in" links: the app's address in the link itself too, for a visitor
  // without JavaScript (the click handler uses WAREXX_CONFIG.appUrl)
  const appHref = String(cfg.appUrl || '/').replace(/&/g, '&amp;').replace(/"/g, '&quot;')
  html = html.split('href="/" data-enter-app').join(`href="${appHref}" data-enter-app`)
  // today's exchange rates for prices abroad (a build only; dev keeps the page's)
  if (mode === 'build') html = withRates(html)
  const head = html.search(/<head[^>]*>/i)
  if (head < 0) throw new Error('landing/index.html: no <head>')
  const at = html.indexOf('>', head) + 1
  return html.slice(0, at)
    + `\n<meta http-equiv="Content-Security-Policy" content="${landingCsp(cfg)}">` + html.slice(at)
}

export function build(out = OUT) {
  rmSync(out, { recursive: true, force: true })
  mkdirSync(out, { recursive: true })
  const html = landingPage('build')
  writeFileSync(path.join(out, 'index.html'), html)
  // what the page loads beside it (the film's video, when there is one)
  if (existsSync(ASSETS)) cpSync(ASSETS, path.join(out, 'assets'), { recursive: true })
  // the address the page had when the app served it; old links keep working
  writeFileSync(path.join(out, 'landing.html'), html)
  // /pricing — what Monthly and Annual open
  mkdirSync(path.join(out, 'pricing'), { recursive: true })
  writeFileSync(path.join(out, 'pricing', 'index.html'), pricingPage(html))
  const cfg = landingConfig('build')
  // The demo on ANOTHER site (an absolute VITE_DEMO_URL): /demo here forwards
  // there — a page for any static host, _redirects for Netlify / Cloudflare
  // Pages. A relative one ("/demo/") is this site's own demo, built beside the
  // page by tools/site.mjs, so nothing forwards.
  const elsewhere = /^https?:\/\//i.test(cfg.demoAppUrl)
  const esc = (v) => String(v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
  if (elsewhere) {
    mkdirSync(path.join(out, 'demo'), { recursive: true })
    writeFileSync(path.join(out, 'demo', 'index.html'), '<!doctype html><meta charset="utf-8">'
      + `<meta http-equiv="refresh" content="0; url=${esc(cfg.demoAppUrl)}"><title>WAREXX demo</title>`
      + `<a href="${esc(cfg.demoAppUrl)}">Open the WAREXX interactive demo</a>\n`)
  }
  writeFileSync(path.join(out, '_redirects'),
    (elsewhere ? `/demo    ${cfg.demoAppUrl}  302\n/demo/*  ${cfg.demoAppUrl}  302\n` : '')
    + `/app     ${cfg.appUrl}  302\n`)
  writeFileSync(path.join(out, '_headers'),
    '/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n'
    + '/index.html\n  Cache-Control: no-cache\n')
  console.log('[landing] built', path.relative(FRONTEND, out), cfg)
  return cfg
}

function dev() {
  const port = +(process.env.PORT || 5174)
  const leadApi = new URL(process.env.LEAD_API_URL || 'http://127.0.0.1:8003')
  createServer((req, res) => {
    const url = new URL(req.url, 'http://x')
    if (url.pathname === '/api/leads' || url.pathname === '/api/trial') {
      const fwd = httpRequest({ hostname: leadApi.hostname, port: leadApi.port, path: url.pathname,
        method: req.method, headers: { ...req.headers, host: leadApi.host } }, (r) => {
        res.writeHead(r.statusCode, r.headers); r.pipe(res)
      })
      fwd.on('error', () => { res.writeHead(502, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ detail: `lead API not running on ${leadApi.href} (python ../lead-api/server.py)` })) })
      return req.pipe(fwd)
    }
    if (url.pathname === '/' || url.pathname === '/index.html' || url.pathname === '/landing.html') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-cache' })
      return res.end(landingPage('dev'))   // re-read on every load: edit, reload
    }
    if (url.pathname === '/pricing' || url.pathname === '/pricing/') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-cache' })
      return res.end(pricingPage(landingPage('dev')))
    }
    if (url.pathname.startsWith('/assets/')) {
      const f = path.join(ASSETS, path.normalize(decodeURIComponent(url.pathname.slice(8))).replace(/^([/\\])+/, ''))
      if (f.startsWith(ASSETS) && existsSync(f) && statSync(f).isFile()) {
        // ranges, so the film's video can be seeked
        const size = statSync(f).size; const type = f.endsWith('.mp4') ? 'video/mp4' : 'application/octet-stream'
        const m = /bytes=(\d*)-(\d*)/.exec(req.headers.range || '')
        if (m) {
          const start = m[1] ? +m[1] : 0; const end = m[2] ? +m[2] : size - 1
          res.writeHead(206, { 'Content-Type': type, 'Content-Range': `bytes ${start}-${end}/${size}`, 'Accept-Ranges': 'bytes', 'Content-Length': end - start + 1 })
          return createReadStream(f, { start, end }).pipe(res)
        }
        res.writeHead(200, { 'Content-Type': type, 'Content-Length': size, 'Accept-Ranges': 'bytes' })
        return createReadStream(f).pipe(res)
      }
    }
    res.writeHead(404, { 'Content-Type': 'text/plain' }); res.end('Not found')
  }).listen(port, () => console.log(`[landing] http://localhost:${port}/  (lead form → ${leadApi.href}api/leads)`))
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const cmd = process.argv[2]
  if (cmd === 'build') build()
  else if (cmd === 'dev') dev()
  else { console.error('usage: node tools/landing.mjs build|dev'); process.exit(2) }
}
