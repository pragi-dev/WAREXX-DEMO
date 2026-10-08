// The live demo's /m: the WAREXX phone app itself.
//
// The phone app is one self-contained page plus its icons, served by the
// production backend from app/backend/app/mobile. The demo uses the snapshot of
// those files in vendor/warexx-app/mobile (tools/sync-app.mjs copies them) as
// they are, and adds two things in front of the app's own script: a
// Content-Security-Policy that keeps every request on the demo's own origin,
// and <script src="/m/demo-boot.js">, which starts the stand-in server and signs
// the visitor in (demo/public/m/demo-boot.js).
//
//   used by vite.demo.config.js (build output, and /m in dev and preview), or
//   node tools/mobile-page.mjs [outDir]   (default: dist/demo)
import { readdirSync, readFileSync, writeFileSync, mkdirSync, existsSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
export const MOBILE_DIR = path.resolve(HERE, '..', 'vendor', 'warexx-app', 'mobile')
const DEMO_PUBLIC_M = path.resolve(HERE, '..', 'demo', 'public', 'm')
const BOOT = '<script src="/m/demo-boot.js"></script>\n'

// connect-src 'self': whatever the page does, it can only talk to the demo's own
// origin, where sw.js answers. No production API is reachable from the demo.
// A <meta> policy only counts inside <head>, so it goes straight after <head>.
export const DEMO_CSP = "connect-src 'self'; form-action 'self'; base-uri 'self'"
const CSP_META = `<meta http-equiv="Content-Security-Policy" content="${DEMO_CSP}">\n`

/** The phone page with the demo's policy and start-up script in front of the app's own. */
export function mobilePage() {
  let html = readFileSync(path.join(MOBILE_DIR, 'index.html'), 'utf8')
  const head = html.search(/<head[^>]*>/i)
  if (head < 0) throw new Error('vendor/warexx-app/mobile/index.html: no <head> for the demo policy')
  const headEnd = html.indexOf('>', head) + 1
  html = html.slice(0, headEnd) + '\n' + CSP_META + html.slice(headEnd)
  const at = html.search(/<script>(?![^<]*src=)/)
  if (at < 0) throw new Error('vendor/warexx-app/mobile/index.html: no inline <script> to start before')
  return html.slice(0, at) + BOOT + html.slice(at)
}

/** Every file of the phone app, as the demo serves it at /m/<name>. */
export function mobileFiles() {
  const out = []
  for (const name of readdirSync(MOBILE_DIR)) {
    const full = path.join(MOBILE_DIR, name)
    if (!statSync(full).isFile()) continue
    out.push({ name, body: name === 'index.html' ? mobilePage() : readFileSync(full) })
  }
  return out
}

export function writeMobile(outDir) {
  const dir = path.join(outDir, 'm')
  mkdirSync(dir, { recursive: true })
  for (const f of mobileFiles()) writeFileSync(path.join(dir, f.name), f.body)
  // demo-boot.js comes from demo/public/m (vite copies it too; this keeps the
  // command-line use self-contained)
  const boot = path.join(DEMO_PUBLIC_M, 'demo-boot.js')
  if (existsSync(boot)) writeFileSync(path.join(dir, 'demo-boot.js'), readFileSync(boot))
}

const TYPES = { '.html': 'text/html; charset=utf-8', '.png': 'image/png', '.js': 'text/javascript',
  '.webmanifest': 'application/manifest+json', '.json': 'application/json', '.svg': 'image/svg+xml' }

/** Connect middleware: /m, its files, and every /m/<screen> address → the page. */
export function mobileMiddleware() {
  return (req, res, next) => {
    const url = new URL(req.url, 'http://x')
    if (!(url.pathname === '/m' || url.pathname.startsWith('/m/'))) return next()
    const rel = url.pathname.replace(/^\/m\/?/, '')
    if (rel && !rel.includes('/')) {
      for (const dir of [DEMO_PUBLIC_M, MOBILE_DIR]) {
        const f = path.join(dir, rel)
        if (existsSync(f) && statSync(f).isFile()) {
          res.setHeader('Content-Type', TYPES[path.extname(f)] || 'application/octet-stream')
          return res.end(rel === 'index.html' && dir === MOBILE_DIR ? mobilePage() : readFileSync(f))
        }
      }
    }
    res.setHeader('Content-Type', TYPES['.html'])
    res.setHeader('Cache-Control', 'no-cache')
    res.end(mobilePage())
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const out = path.resolve(process.argv[2] || path.join(HERE, '..', 'dist', 'demo'))
  writeMobile(out)
  console.log('phone app written to', path.join(out, 'm'))
}
