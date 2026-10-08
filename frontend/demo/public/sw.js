/* WAREXX static demo — the "server".
 *
 * The demo is the real WAREXX app with no backend behind it. This service
 * worker answers every request the app makes to /api, /pos and /m from answers
 * recorded off a real server running a sample business (tools/record.mjs wrote
 * data/manifest.json and data/bodies/*). Nothing is sent anywhere; nothing is
 * stored. Writes are acknowledged so the screens behave, and the recorded data
 * comes back on the next read — the demo resets itself on every reload.
 */
const VERSION = 'wx-demo-v3'
const DATA = new URL('./data/', self.registration.scope).href

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()))

let manifestP = null
const manifest = () => (manifestP ||= fetch(DATA + 'manifest.json', { cache: 'no-cache' })
  // no recording yet (or one being written): an empty one, so the app still opens
  .then((r) => (r.ok ? r.json() : { entries: [] })).catch(() => ({ entries: [] }))
  .then((m) => {
    // index: exact key, then path without query, then a path "shape" with the
    // numbers taken out — so /api/documents/7 still finds a document when only
    // /api/documents/3 was recorded.
    const exact = new Map(), byPath = new Map(), byShape = new Map()
    for (const e of m.entries) {
      exact.set(e.key, e)
      push(byPath, `${e.method} ${e.wh} ${e.path}`, e)
      push(byPath, `${e.method} * ${e.path}`, e)
      push(byShape, `${e.method} ${e.wh} ${shape(e.path)}`, e)
      push(byShape, `${e.method} * ${shape(e.path)}`, e)
    }
    return { meta: m.meta || {}, exact, byPath, byShape }
  }))
const push = (map, k, v) => { const a = map.get(k); a ? a.push(v) : map.set(k, [v]) }
const shape = (p) => p.replace(/\/\d+(?=\/|$)/g, '/:n').replace(/\/[0-9a-f]{8,}(?=\/|$)/gi, '/:h')

// /api/leads is the one real endpoint on a site that serves the landing page and
// the demo together (npm run build:site): the landing page's forms must reach it.
// It only sends an email; nothing of the demo goes there.
const LEADS = '/api/leads'
const ours = (url) => url.origin === self.location.origin && url.pathname !== LEADS &&
  (url.pathname.startsWith('/api/') || url.pathname === '/api' || url.pathname.startsWith('/pos'))

// The phone app (/m) is real files — the WAREXX phone page, copied in at build
// (tools/mobile-page.mjs) — not a recording. Its screens have addresses of their
// own (/m/grn/12, /m/purchase-orders), which are all that one page.
const isMobilePage = (url) => url.origin === self.location.origin &&
  (url.pathname === '/m' || url.pathname.startsWith('/m/')) && !/\.[a-z0-9]+$/i.test(url.pathname)

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)
  // a page load re-reads the recording, so newly recorded data shows on reload
  // without waiting for this worker to be replaced
  if (event.request.mode === 'navigate' && url.origin === self.location.origin
      && !url.pathname.startsWith('/pos')) manifestP = null
  if (event.request.mode === 'navigate' && isMobilePage(url)) {
    event.respondWith(fetch('/m/index.html', { cache: 'no-cache' }))
    return
  }
  if (!ours(url)) return                      // the app's own files: straight through
  event.respondWith(answer(event.request, url))
})

// ---- Ask WAREXX (tools/record_mobile.py recorded its answers) ----
// A question is a POST whose meaning is in its body, so it is looked up by a key
// made from the body: the words (normalised as the recorder normalised them) and
// the previous answer's intent — "only in warehouse b" means something different
// after "low stock" than after "pending GRNs" — or, for a tap, the intent itself.
// Spoken words wander ("show me the low stock items please"), so a close-enough
// recorded question answers too. One that is not close to anything recorded is
// told so, with questions that are — never answered with something else.
const CMD = '/api/assist/command'
const norm = (s) => String(s || '').toLowerCase().replace(/[’']/g, '')
  .replace(/[^a-z0-9\- ]+/g, ' ').split(/\s+/).filter(Boolean).join(' ')
  .replace(/^(?:(?:hey|ok|okay) )?warexx /, '').replace(/^please /, '').replace(/ please$/, '')
  .replace(/^show me /, 'show ')
const canon = (i) => {
  const f = Object.entries(i.filters || {}).filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${v}`).sort().join(';')
  return `${i.intent}|${f}|${i.all ? 1 : 0}`
}
// Filler that carries no meaning in a question about the business: "which
// products are low in stock" asks what "show low stock items" asks.
const STOP = new Set(['the', 'a', 'an', 'me', 'my', 'all', 'please', 'show', 'is', 'are', 'what', 'whats', 'do', 'we', 'have',
  'which', 'in', 'of', 'any', 'there', 'items', 'list', 'currently'])
const words = (t) => new Set(t.split(' ').filter((w) => !STOP.has(w)))
const dice = (a, b) => {
  if (!a.size || !b.size) return 0
  let n = 0; for (const w of a) if (b.has(w)) n++
  return (2 * n) / (a.size + b.size)
}
let askIndexFor = null, askIndex = null
function askKeys(m) {
  if (askIndexFor !== m) {
    askIndexFor = m; askIndex = new Map()
    for (const k of m.exact.keys()) {
      const mm = k.match(/^POST (\S+) \/api\/assist\/command#T:([^:]+):(.*)$/)
      if (!mm) continue
      const key = `${mm[1]} ${mm[2]}`
      if (!askIndex.has(key)) askIndex.set(key, [])
      askIndex.get(key).push({ text: mm[3], words: words(mm[3]), key: k })
    }
  }
  return askIndex
}
async function assistAnswer(m, req, wh) {
  let body = {}
  try { body = await req.clone().json() } catch { /* empty */ }
  const W = wh === '-' ? '1' : wh
  const get = (k) => m.exact.get(`POST ${W} ${CMD}#${k}`)
  let hit = null
  if (body.intent && body.intent.intent) {
    hit = get('I:' + canon(body.intent))
  } else {
    const t = norm(body.text)
    const prev = (body.context && body.context.intent && body.context.intent.intent) || '-'
    // "open the second one" / "receive the first one" point INTO the last answer,
    // whose rows travel in the request (context.items) — so they are answered
    // from those rows, exactly as the server answers them, not from a recording
    // made after some other list.
    const ORD = { first: 0, '1st': 0, second: 1, '2nd': 1, third: 2, '3rd': 2, fourth: 3, '4th': 3, fifth: 4, '5th': 4, last: -1 }
    const pm = t.match(/^(open|show|select|view|pick|take|receive)(?: me)?(?: the)? (first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)(?: one)?$/)
      || t.match(/^(open|show|receive) (?:it|that|this)$/)
    const rows = (body.context && Array.isArray(body.context.items)) ? body.context.items : []
    if (pm && rows.length) {
      let i = pm[2] ? ORD[pm[2]] : 0
      if (i < 0) i = rows.length - 1
      const it = rows[i]
      const card = (kind, line, extra) => json({ ok: kind === 'open', kind, title: 'Open', line, text: body.text || '',
        items: [], metrics: [], actions: [], total: 0, intent: { intent: 'PICK', filters: {} }, context: body.context, ...extra })
      if (!it) return card('empty', `The last answer had ${rows.length} row${rows.length === 1 ? '' : 's'}.`)
      if (pm[1] === 'receive' && it.kind === 'transfer') {
        const r = get('I:' + canon({ intent: 'RECEIVE_TRANSFER', filters: { transfer_id: it.id } }))
        if (r) { const j = await (await replay(r)).json(); j.text = body.text || ''; return json(j) }
      }
      if (it.open) return card('open', `Opening ${it.code || 'it'}.`, { title: 'Opening', open: it.open })
      return card('empty', 'That one has no screen on the phone — it’s on the desktop app.')
    }
    hit = (prev !== '-' && get(`T:${prev}:${t}`)) || get(`T:-:${t}`)
    if (!hit && t) {
      const idx = askKeys(m), want = words(t)
      let best = null, score = 0
      for (const p of (prev !== '-' ? [prev, '-'] : ['-'])) {
        for (const c of idx.get(`${W} ${p}`) || []) {
          const s = dice(want, c.words) + (p === prev && p !== '-' ? 0.01 : 0)
          if (s > score) { score = s; best = c }
        }
      }
      if (best && score >= 0.8) hit = m.exact.get(best.key)
    }
  }
  if (hit) {
    const r = await (await replay(hit)).json()
    r.text = body.text || ''
    r.source = body.source || (body.text ? 'text' : 'tap')
    return json(r)
  }
  const ov = m.exact.get(`GET ${W} /api/assist/overview`)
  let sugg = ['What needs my attention today?', 'Show pending GRNs', 'Show low stock items', 'Show pending purchase orders']
  try { if (ov) sugg = (await (await replay(ov)).json()).suggestions || sugg } catch { /* defaults */ }
  return json({ ok: false, demo: true, kind: 'unknown', title: 'Not part of this demo',
    line: 'The demo answers a set of questions about its sample business. Try one of these:',
    text: body.text || '', items: [], metrics: [], total: 0,
    actions: sugg.slice(0, 5).map((s) => ({ label: s, ask: s })),
    intent: { intent: 'NONE', filters: {} }, context: body.context || {} })
}

// ---- product search: the recorded catalogue, filtered as the server filters ----
// One recording of the whole catalogue per warehouse; a search is that list with
// the same four fields matched, so typing "shirt" shows shirts rather than the
// unfiltered first page.
async function productsAnswer(m, url, wh) {
  const q = (url.searchParams.get('q') || '').trim().toLowerCase()
  const status = url.searchParams.get('status') || 'all'
  const base = m.exact.get(`GET ${wh} /api/inventory/products?status=all`) || m.exact.get(`GET 1 /api/inventory/products?status=all`)
  if (!base) return null
  let rows = await (await replay(base)).json()
  if (status === 'pending') rows = rows.filter((p) => !p.detailed)
  if (status === 'detailed') rows = rows.filter((p) => p.detailed)
  rows = rows.filter((p) => [p.description, p.sku, p.barcode, p.hsn].some((f) => String(f || '').toLowerCase().includes(q)))
  const off = +(url.searchParams.get('offset') || 0), lim = +(url.searchParams.get('limit') || 0)
  return json(lim ? rows.slice(off, off + lim) : rows.slice(off))
}

// ---- the Item Locator (tools/record_locator.py recorded its answers) ----
// One recorded answer per SKU and per carton label, and an index of every
// per-piece code -> its product and the piece's own number and status. A piece
// is answered as the real server answers it: its product, plus `unit`.
const LOCATE = '/api/inventory/locate'
let unitsP = null, unitsFor = null
const unitIndex = (m) => {
  if (unitsFor !== m) {
    unitsFor = m
    const e = m.exact.get(`GET - ${LOCATE}/units`)
    unitsP = e ? replay(e).then((r) => r.json()).catch(() => ({})) : Promise.resolve({})
  }
  return unitsP
}
async function locateAnswer(m, url) {
  const raw = (url.searchParams.get('code') || '').trim()
  if (!raw) return json({ detail: 'give a code to look up' }, 400)
  // the demo shows the sample codes as SMPL-; the same items printed as ESSA-
  // (the app's own prefix) are the same items, so both are read
  const norm = (c) => c.trim().toUpperCase().replace(/^ESSA-/, 'SMPL-')
  // a scanned QR carries the code among other fields (E1|66|SMPL-00066|…):
  // the whole text first, then each part of it
  const tries = [raw, ...raw.split(/[|,;\s]+/)].map(norm).filter(Boolean)
  const units = await unitIndex(m)
  for (const c of tries) {
    const hit = m.exact.get(`GET - ${LOCATE}?code=${c}`)
    if (hit) return replay(hit)
    const u = units[c]
    if (u) {
      const p = m.exact.get(`GET - ${LOCATE}?code=${u.sku}`)
      if (p) {
        const body = await replay(p).then((r) => r.json())
        return json({ ...body, code: c, unit: { id: null, code: c, seq: u.seq, status: u.status, printed: null } })
      }
    }
  }
  return json({ detail: `Nothing matches '${raw}'. It is not a product QR, a piece code, `
    + 'a carton label, a barcode or a SKU.' }, 404)
}

async function answer(req, url) {
  const m = await manifest()
  const method = req.method.toUpperCase()
  const wh = req.headers.get('X-Essa-Warehouse') || '-'
  const path = url.pathname
  const key = `${method} ${wh} ${path}${url.search}`
  // The Item Locator answers ONE code, so it is never matched loosely: a code
  // nobody recorded must say "nothing matches", not replay some other item.
  if (method === 'GET' && path === LOCATE) return locateAnswer(m, url)
  if (method === 'POST' && path === CMD) return assistAnswer(m, req, wh)
  // a search the recording has no exact answer for: the catalogue, filtered
  if (method === 'GET' && path === '/api/inventory/products' && (url.searchParams.get('q') || '').trim()
      && !m.exact.get(key)) {
    const r = await productsAnswer(m, url, wh)
    if (r) return r
  }
  // a code lookup answers that code or nothing — never a neighbouring product
  if (method === 'GET' && path === '/api/inventory/lookup') {
    const c = (url.searchParams.get('code') || '').trim().toUpperCase().replace(/^ESSA-/, 'SMPL-')
    const e = m.exact.get(`GET - ${path}?code=${c}`)
    return e ? replay(e) : json({ detail: `No product for '${url.searchParams.get('code')}'` }, 404)
  }

  const hit = m.exact.get(key) || m.exact.get(`${method} - ${path}${url.search}`)
    || pick(m.byPath.get(`${method} ${wh} ${path}`), url)
    || pick(m.byPath.get(`${method} * ${path}`), url)
    || pick(m.byShape.get(`${method} ${wh} ${shape(path)}`), url)
    || pick(m.byShape.get(`${method} * ${shape(path)}`), url)
  if (hit) return replay(hit)
  // Signing in never depends on the recording: whatever is typed, the visitor is
  // the demo's Sample Admin. (A recorded answer, above, wins when there is one.)
  if (AUTH[path]) return json(AUTH[path])

  if (method === 'GET' || method === 'HEAD') return missingRead(path)
  return acknowledgeWrite(req, path, m.meta)
}

// Of several recorded answers for one path, the one whose query shares the
// most parameters with this request (dates move on; the filter usually doesn't).
function pick(list, url) {
  if (!list || !list.length) return null
  if (list.length === 1) return list[0]
  const want = new URLSearchParams(url.search)
  let best = list[0], score = -1
  for (const e of list) {
    const got = new URLSearchParams(e.search || '')
    let s = 0
    for (const [k, v] of want) if (got.get(k) === v) s += 2; else if (got.has(k)) s += 1
    if (s > score) { best = e; score = s }
  }
  return best
}

async function replay(e) {
  const headers = { 'Content-Type': e.type || 'application/json', 'X-Warexx-Demo': '1' }
  if (e.disposition) headers['Content-Disposition'] = e.disposition
  if (e.location) headers.Location = e.location
  if (e.body !== undefined) return new Response(e.body, { status: e.status, headers })
  const r = await fetch(DATA + 'bodies/' + e.file)
  return new Response(await r.arrayBuffer(), { status: e.status, headers })
}

function missingRead(path) {
  if (path.startsWith('/pos') || path === '/m' || path.startsWith('/m/')) {
    return new Response(page('This screen is not part of the demo',
      'Everything else in the menu is — pick another screen to keep exploring.'),
      { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } })
  }
  // A list nobody recorded is an empty list, not an error: the screen shows its
  // own "nothing here yet" instead of a failure.
  return json([], 200)
}

const SESSION = { ok: true, token: 'demo', user: 'superadmin', role: 'superadmin',
  role_label: 'Super Admin', full_name: 'Sample Admin', permissions: {},
  can: { manage_users: true, admin: true, command: true, boss: false } }
const AUTH = {
  '/api/auth/login': SESSION,
  '/api/auth/verify': SESSION,
  '/api/auth/me': { id: 1, username: 'superadmin', role: 'superadmin', role_label: 'Super Admin',
    full_name: 'Sample Admin', active: true, permissions: {} },
  '/api/auth/logout': { ok: true },
}

let nextId = 90000
async function acknowledgeWrite(req, path, meta) {
  if (path.startsWith('/pos')) {
    // a shop form: back to the page it came from, which still shows the sample
    return new Response(null, { status: 303, headers: { Location: req.referrer || '/pos/' } })
  }
  if (path === '/api/auth/logout') return json({ ok: true })
  let body = {}
  try { body = await req.clone().json() } catch { /* a form or a file */ }
  const id = ++nextId
  // Echo what was sent with an id and an "ok", which is what most screens read
  // back from a save. The message is what a toast shows if the screen surfaces it.
  return json({ ok: true, demo: true, id, ...(body && typeof body === 'object' ? body : {}),
    message: meta.writeMessage || 'Saved in the demo — sample data resets when you reload.' })
}

const json = (v, status = 200) => new Response(JSON.stringify(v), {
  status, headers: { 'Content-Type': 'application/json', 'X-Warexx-Demo': '1' } })

const page = (title, text) => `<!doctype html><meta charset="utf-8"><body style="font:15px/1.6 Inter,system-ui,sans-serif;color:#1A1814;background:#F7F5F1;display:grid;place-items:center;min-height:90vh;margin:0"><div style="text-align:center;max-width:420px;padding:24px"><h2 style="margin:0 0 8px">${title}</h2><p style="color:#6E675E;margin:0">${text}</p></div>`
