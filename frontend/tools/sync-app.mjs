// Refresh the demo's copy of the WAREXX app from app/ — run it on purpose,
// never as part of a build:
//
//     npm run sync:app            (from landing-demo/frontend)
//
// The live demo is the real WAREXX app with sw.js standing in for the server.
// It runs a COPY of the app (vendor/warexx-app), not app/frontend/src itself,
// for two reasons:
//
//   1. Independent deployment. landing-demo builds and deploys on its own: a
//      host building it needs nothing outside landing-demo/, and a change to
//      the production app cannot break the public demo by accident.
//   2. The recording (demo-data/recorded) was made against one version of the
//      app's screens. A newer screen may ask for an address nobody recorded and
//      show an empty list. Copy the app and re-record together.
//
// What it copies:
//   app/frontend/src     → vendor/warexx-app/src      (the desktop app)
//   app/backend/app/mobile → vendor/warexx-app/mobile  (the phone app at /m)
// and writes vendor/warexx-app/SNAPSHOT.json saying when and what.
import { cpSync, existsSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync, mkdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const REPO = path.resolve(FRONTEND, '..', '..')
const APP = process.env.WAREXX_APP_DIR ? path.resolve(process.env.WAREXX_APP_DIR) : path.join(REPO, 'app')
const OUT = path.join(FRONTEND, 'vendor', 'warexx-app')

const PARTS = [
  { from: path.join(APP, 'frontend', 'src'), to: 'src' },
  { from: path.join(APP, 'backend', 'app', 'mobile'), to: 'mobile' },
]

for (const p of PARTS) {
  if (!existsSync(p.from)) {
    console.error(`[sync-app] ${p.from} not found. Set WAREXX_APP_DIR to the app/ folder.`)
    process.exit(1)
  }
}

const files = (dir) => readdirSync(dir).flatMap((n) => {
  const f = path.join(dir, n)
  return statSync(f).isDirectory() ? files(f) : [f]
})

rmSync(OUT, { recursive: true, force: true })
mkdirSync(OUT, { recursive: true })
const hash = createHash('sha256')
let count = 0
for (const p of PARTS) {
  cpSync(p.from, path.join(OUT, p.to), { recursive: true })
  for (const f of files(path.join(OUT, p.to)).sort()) {
    hash.update(path.relative(OUT, f).split(path.sep).join('/'))
    hash.update(readFileSync(f))
    count++
  }
}
writeFileSync(path.join(OUT, 'SNAPSHOT.json'), JSON.stringify({
  copiedAt: new Date().toISOString(),
  from: PARTS.map((p) => path.relative(REPO, p.from).split(path.sep).join('/')),
  files: count,
  sha256: hash.digest('hex'),
  note: 'Copied by tools/sync-app.mjs. Do not edit here: change app/ and sync again.',
}, null, 2) + '\n')
console.log(`[sync-app] ${count} files copied into vendor/warexx-app`)
