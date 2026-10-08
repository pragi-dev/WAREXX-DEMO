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
import { createServer, request as httpRequest } from 'node:http'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const SRC = path.join(FRONTEND, 'landing', 'index.html')
const OUT = path.join(FRONTEND, 'dist', 'landing')

const DEFAULTS = {
  dev: { VITE_LEAD_ENDPOINT: '/api/leads', VITE_DEMO_URL: 'http://localhost:8002/', VITE_APP_URL: 'http://localhost:8000/' },
  build: { VITE_LEAD_ENDPOINT: '/api/leads', VITE_DEMO_URL: 'https://demo.warexx.aavoraa.com/', VITE_APP_URL: 'https://app.warexx.aavoraa.com/' },
}
const KEYS = { VITE_LEAD_ENDPOINT: 'formEndpoint', VITE_DEMO_URL: 'demoAppUrl', VITE_APP_URL: 'appUrl' }

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
  // "Sign in" links: the app's address in the link itself too, for a visitor
  // without JavaScript (the click handler uses WAREXX_CONFIG.appUrl)
  const appHref = String(cfg.appUrl || '/').replace(/&/g, '&amp;').replace(/"/g, '&quot;')
  html = html.split('href="/" data-enter-app').join(`href="${appHref}" data-enter-app`)
  const head = html.search(/<head[^>]*>/i)
  if (head < 0) throw new Error('landing/index.html: no <head>')
  const at = html.indexOf('>', head) + 1
  return html.slice(0, at)
    + `\n<meta http-equiv="Content-Security-Policy" content="${landingCsp(cfg)}">` + html.slice(at)
}

function build() {
  rmSync(OUT, { recursive: true, force: true })
  mkdirSync(OUT, { recursive: true })
  const html = landingPage('build')
  writeFileSync(path.join(OUT, 'index.html'), html)
  // the address the page had when the app served it; old links keep working
  writeFileSync(path.join(OUT, 'landing.html'), html)
  // /demo on the public site → the interactive demo, which must live at the
  // root of its own origin (its service worker answers /api there). A page for
  // any static host; _redirects for Netlify / Cloudflare Pages; vercel.json has
  // the same rule.
  const cfg = landingConfig('build')
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
  mkdirSync(path.join(OUT, 'demo'), { recursive: true })
  writeFileSync(path.join(OUT, 'demo', 'index.html'), '<!doctype html><meta charset="utf-8">'
    + `<meta http-equiv="refresh" content="0; url=${esc(cfg.demoAppUrl)}"><title>WAREXX demo</title>`
    + `<a href="${esc(cfg.demoAppUrl)}">Open the WAREXX interactive demo</a>\n`)
  writeFileSync(path.join(OUT, '_redirects'),
    `/demo    ${cfg.demoAppUrl}  302\n/demo/*  ${cfg.demoAppUrl}  302\n/app     ${cfg.appUrl}  302\n`)
  writeFileSync(path.join(OUT, '_headers'),
    '/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n'
    + '/index.html\n  Cache-Control: no-cache\n')
  console.log('[landing] built dist/landing', cfg)
}

function dev() {
  const port = +(process.env.PORT || 5174)
  const leadApi = new URL(process.env.LEAD_API_URL || 'http://127.0.0.1:8003')
  createServer((req, res) => {
    const url = new URL(req.url, 'http://x')
    if (url.pathname === '/api/leads') {
      const fwd = httpRequest({ hostname: leadApi.hostname, port: leadApi.port, path: '/api/leads',
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
    res.writeHead(404, { 'Content-Type': 'text/plain' }); res.end('Not found')
  }).listen(port, () => console.log(`[landing] http://localhost:${port}/  (lead form → ${leadApi.href}api/leads)`))
}

const cmd = process.argv[2]
if (cmd === 'build') build()
else if (cmd === 'dev') dev()
else if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.error('usage: node tools/landing.mjs build|dev'); process.exit(2)
}
