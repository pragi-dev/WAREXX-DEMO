// Rebuild the live demo's data in one go:
//   seed a scratch database with the sample business → serve it with the real
//   backend → record every screen (record.mjs) → stop the server.
//
//   node landing-demo/demo-data/tools/build-data.mjs            (from the repo root)
//
// Work files go to landing-demo/demo-data/demo-build/ (git-ignored). Then build the
// demo itself:  cd landing-demo/frontend && npm run build:demo
import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, rmSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '..', '..', '..')
const BACKEND = path.join(REPO, 'app', 'backend')
const PY = [path.join(BACKEND, '.venv', 'Scripts', 'python.exe'), path.join(BACKEND, '.venv', 'bin', 'python')]
  .find((p) => existsSync(p)) || 'python'
const WORK = path.resolve(process.env.DEMO_WORK || path.join(HERE, '..', 'demo-build'))
const PORT = +(process.env.DEMO_REC_PORT || 8020)
const fwd = (p) => p.split(path.sep).join('/')
const log = (...a) => console.log('[build-data]', ...a)

if (!fwd(WORK).includes('demo-build')) throw new Error('DEMO_WORK must be a folder called demo-build')
rmSync(WORK, { recursive: true, force: true })
mkdirSync(WORK, { recursive: true })

const env = {
  ...process.env,
  ESSA_DATABASE_URL: `sqlite:///${fwd(path.join(WORK, 'demo.db'))}`,
  DATABASE_URL: `sqlite:///${fwd(path.join(WORK, 'shop.db'))}`,
  ESSA_WAREHOUSE_DB: path.join(WORK, 'demo.db'),
  ESSA_STATE_DIR: WORK,
  ESSA_COMPANY_NAME: 'Sample Company',
  ESSA_COMPANY_GSTIN: '33AAACS0000A1Z5',
  SHOP_NAME: 'Sample Company',
  ANTHROPIC_API_KEY: '',            // nothing is read by a paid model while recording
  PYTHONIOENCODING: 'utf-8',
}

// order matters: the base first, admin last (its activity trail describes the rest)
for (const s of ['seed_base.py', 'seed_purchasing.py', 'seed_operations.py', 'seed_shop.py', 'seed_admin.py']) {
  log('seeding:', s)
  const r = spawnSync(PY, [path.join(HERE, s)], { cwd: BACKEND, env, stdio: 'inherit' })
  if (r.status !== 0) throw new Error(`${s} failed (exit ${r.status})`)
}

log(`starting the recording server on :${PORT}`)
const server = spawn(PY, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(PORT)],
  { cwd: BACKEND, env, stdio: 'ignore' })
const stop = () => { try { server.kill() } catch {} }
process.on('exit', stop)
let up = false
for (let i = 0; i < 120 && !up; i++) {
  try { up = (await fetch(`http://127.0.0.1:${PORT}/api/status`)).ok } catch {}
  if (!up) await new Promise((r) => setTimeout(r, 1000))
}
if (!up) { stop(); throw new Error('the recording server did not start') }

log('recording')
const rec = spawnSync(process.execPath, [path.join(HERE, 'record.mjs'), `http://127.0.0.1:${PORT}`],
  { env, stdio: 'inherit' })
stop()
if (rec.status !== 0) throw new Error(`record.mjs failed (exit ${rec.status})`)
log('done — now: cd landing-demo/frontend && npm run build:demo')
