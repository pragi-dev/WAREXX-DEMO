// The guided tour of the demo: ten real WAREXX features, each highlighted on the
// live screen it belongs to, with a tooltip beside it.
//
// It starts by itself the first time a visitor opens the demo, and never again
// once they finish or skip it. "Product tour" in the demo's banner runs it again.
// The visitor can leave at any step (Skip tour, ×, or Esc): the tour closes, and
// nothing else changes — no sign-out, no reset, nothing undone.
//
// Real, not a slideshow: each step opens the actual screen by pressing the app's
// own menu items, then highlights the real element there. The page underneath
// stays usable the whole time — the dimming lets clicks through — so a visitor
// can try the feature being shown before moving on. A step whose element is not
// on screen (a narrow phone, a screen that changed) is still explained, in the
// middle of the screen, rather than pointing at nothing.
//
// Where the state lives: in this browser, under the signed-in trial user's email
// (wx_tour:<email>) — the demo has no database (see trial.jsx), so the browser
// that holds the visitor's demo account is also what remembers their tour.
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const visible = (el) => !!el && el.getClientRects().length > 0 && el.getBoundingClientRect().width > 0

// ---- finding the app's own controls ------------------------------------------
const navItem = (label) => [...document.querySelectorAll('.wxnav .navitem')]
  .find((e) => e.textContent.trim().replace(/\d+$/, '').trim() === label)
/** Open a screen the way a person would: its menu item (and its group first,
 *  when the item is folded away inside one). Works with the menu hidden too —
 *  on a phone the items are in the page, behind the ☰ drawer. */
async function openScreen(label, group) {
  let el = navItem(label)
  if (!el && group) { navItem(group)?.click(); await sleep(250); el = navItem(label) }
  if (group && el && !visible(el) && visible(navItem(group))) { navItem(group).click(); await sleep(250) }
  el?.click()
  await sleep(450)
}
const button = (text) => [...document.querySelectorAll('button, label.btn, a.btn')]
  .find((b) => visible(b) && b.textContent.trim() === text)
/** The tour shows a warehouse's screens. Someone in the Central (whole company)
 *  view has no warehouse open, so the tour opens the sample warehouse the demo
 *  starts in — the way the demo itself does — and picks up again at this step
 *  once the page has reloaded into it. */
async function ensureWarehouse(ctx) {
  if (navItem('Dashboard')) return
  const ws = [...document.querySelectorAll('.wx-wsseg-btn')].find((b) => b.textContent.trim() === 'Warehouse')
  let open = null
  try { open = localStorage.getItem('essa_warehouse') } catch { /* private mode */ }
  if (open && ws) { ws.click(); await sleep(600); if (navItem('Dashboard')) return }
  try {
    localStorage.setItem('essa_warehouse', JSON.stringify(ctx.warehouse))
    localStorage.setItem('essa_tab', 'dashboard')
    sessionStorage.setItem('wx_tour_resume', String(ctx.step))
  } catch { return }
  location.reload()
  await new Promise(() => {})               // the page is going away
}
async function openInvoiceList(ctx) {
  await ensureWarehouse(ctx)
  await openScreen('Invoice Entry')
  const back = button('← Invoices')            // a bill left open earlier
  if (back) { back.click(); await sleep(400) }
}
async function openFirstInvoice(ctx) {
  await openInvoiceList(ctx)
  const row = [...document.querySelectorAll('table.ivc-table tbody tr')].find(visible)
  if (row) { row.click(); await sleep(600) }
}
/** the first of these selectors with something visible, as a list */
const first = (...sels) => () => { for (const s of sels) { const e = [...document.querySelectorAll(s)].find(visible); if (e) return [e] } return [] }
/** every visible match of these selectors, highlighted together */
const all = (...sels) => () => sels.flatMap((s) => [...document.querySelectorAll(s)].filter(visible))

// ---- the ten steps: features that exist in WAREXX today ----------------------
export const STEPS = [
  { id: 'welcome', title: 'Welcome to WAREXX',
    body: 'WAREXX runs purchasing, warehouses, stock and stores in one place — from the supplier’s bill to the shelf to the till. This tour shows you ten things you can do here, on a sample business. It takes about two minutes.',
    go: async (ctx) => { await ensureWarehouse(ctx); await openScreen('Dashboard') } },
  { id: 'dashboard', title: 'Your operations at a glance',
    body: 'The dashboard follows every consignment through its stages — ordered, in transport, invoiced, receiving, in stock, dispatched — with the counts for this warehouse. The buttons above it start the day’s common tasks: add an invoice, transfer stock, ring up a sale, raise a PO.',
    go: async (ctx) => { await ensureWarehouse(ctx); await openScreen('Dashboard') }, target: first('.wxd-flow', '.wx-ph-acts') },
  { id: 'navigation', title: 'Every module, one menu',
    body: 'Procurement, inventory, stock audits, dispatch, sales, invoices, labels, reports and payments all live in this menu. The Central / Warehouse / Store switch at the top changes which part of the business you are working in.',
    go: async (ctx) => { await ensureWarehouse(ctx); await openScreen('Dashboard') }, target: first('.wxnav-panel', '.wxnav', '.th-icon') },
  { id: 'create', title: 'Create a purchase order',
    body: 'Orders start here. “New order” raises a purchase order to a supplier; when the goods arrive, the receipt is matched against it. Go ahead and open it — nothing you enter in the demo is kept.',
    go: async (ctx) => { await ensureWarehouse(ctx); await openScreen('Purchase Orders', 'Procurement') }, target: first('.pagehead .btn.primary') },
  { id: 'manage', title: 'Find and filter supplier bills',
    body: 'Every supplier invoice is listed here. Search by supplier, invoice number or status, and use the chips to narrow the list to what needs you — bills to review, being received, already in stock, or short.',
    go: openInvoiceList, target: all('.ivc-head .searchbox', '.ivc-bar .chiprow') },
  { id: 'detail', title: 'Every bill in detail',
    body: 'Opening a bill shows what was read from it — supplier and buyer, invoice and transport details, every line with its taxes and totals — next to the bill itself, so it can be checked before the goods are taken into stock.',
    go: openFirstInvoice, target: first('.invpage .invhead', '.invpage') },
  { id: 'reports', title: '108 reports, ready to use',
    body: 'Every register in WAREXX is a report here — stock, purchases, transport, payments, sales — filtered by date or warehouse and exportable. You can also just ask a question in plain words above the report.',
    go: async (ctx) => { await ensureWarehouse(ctx); await openScreen('Reports') }, target: first('.rp-sidehead', '.sidebar') },
  { id: 'ask', title: 'Ask WAREXX',
    body: 'Ask in plain English or Tamil — typed or spoken — “today’s total invoices”, “which products are low in stock?” — and WAREXX answers from your own data.',
    target: first('.wx-askbtn') },
  { id: 'alerts', title: 'Notifications and your profile',
    body: 'The bell collects what needs attention — stock running low, bills waiting, consignments overdue. Your profile menu holds your password, the invoice-reading settings and “Jump to a screen” (Ctrl K).',
    target: all('.bell', '.th-user') },
  { id: 'workflow', title: 'From supplier bill to stock',
    body: 'The heart of WAREXX: upload a supplier’s invoice — a photo or a PDF. WAREXX reads it, you check what it read, and receiving the goods creates the GRN, prints the QR labels and puts the stock on the shelf. Try it with any bill.',
    go: openInvoiceList, target: () => { const b = button('Upload invoice'); return b ? [b] : [] } },
]

// ---- remembering, per demo user ----------------------------------------------
const key = (user) => 'wx_tour:' + (user || 'demo')
export const tourState = {
  get: (user) => { try { return localStorage.getItem(key(user)) || 'new' } catch { return 'new' } },
  set: (user, v) => { try { localStorage.setItem(key(user), v) } catch { /* private mode */ } },
}
/** Ask the tour to run again (the banner's "Product tour"). */
export const startTour = () => window.dispatchEvent(new Event('wx:tour-start'))

// ---- placing the tooltip -------------------------------------------------------
const PAD = 6, GAP = 12
function unionRect(els) {
  const rs = els.map((e) => e.getBoundingClientRect()).filter((r) => r.width > 0 && r.height > 0)
  if (!rs.length) return null
  const l = Math.min(...rs.map((r) => r.left)), t = Math.min(...rs.map((r) => r.top))
  const r = Math.max(...rs.map((x) => x.right)), b = Math.max(...rs.map((x) => x.bottom))
  return { left: l - PAD, top: t - PAD, width: r - l + 2 * PAD, height: b - t + 2 * PAD }
}
function place(rect, box, vw, vh) {
  if (!rect || vw < 640) return null                  // centred / docked at the bottom on a phone
  const w = box.width, h = box.height, fits = (x, y) => x >= 8 && y >= 8 && x + w <= vw - 8 && y + h <= vh - 8
  const cx = rect.left + rect.width / 2 - w / 2, cy = rect.top + rect.height / 2 - h / 2
  const clampX = (x) => Math.min(Math.max(8, x), vw - w - 8), clampY = (y) => Math.min(Math.max(8, y), vh - h - 8)
  const tries = [
    [clampX(cx), rect.top + rect.height + GAP],          // below
    [clampX(cx), rect.top - h - GAP],                    // above
    [rect.left + rect.width + GAP, clampY(cy)],          // right
    [rect.left - w - GAP, clampY(cy)],                   // left
  ]
  for (const [x, y] of tries) if (fits(x, y)) return { left: x, top: y }
  return { left: clampX(cx), top: vh - h - 16 }         // the target fills the screen: at its foot
}

// ---- the tour ------------------------------------------------------------------
/** Shows the tour on top of the app. Mounted once, beside <App/>. */
export function TourHost({ user, warehouse }) {
  const [step, setStep] = useState(-1)        // -1: not running; STEPS.length: the "all set" card
  const [rect, setRect] = useState(null)
  const [missing, setMissing] = useState(false)
  const [pos, setPos] = useState(null)
  const box = useRef(null)
  const els = useRef([])
  const run = useRef(0)                       // the step being prepared, so a stale one is dropped

  // first visit: start by itself once the app has drawn its menu — or carry on
  // at the step the tour was on when it reloaded into the sample warehouse
  useEffect(() => {
    let alive = true
    let resume = null
    try { resume = sessionStorage.getItem('wx_tour_resume'); sessionStorage.removeItem('wx_tour_resume') } catch { /* private mode */ }
    if (resume !== null && !isNaN(+resume)) {
      (async () => {
        for (let i = 0; i < 40 && !document.querySelector('.wxnav .navitem'); i++) await sleep(150)
        await sleep(500)
        if (alive) setStep(Math.min(Math.max(0, +resume), STEPS.length - 1))
      })()
    } else if (tourState.get(user) === 'new') {
      (async () => {
        for (let i = 0; i < 40 && !document.querySelector('.wxnav .navitem'); i++) await sleep(150)
        await sleep(700)
        if (alive && tourState.get(user) === 'new') setStep(0)
      })()
    }
    const again = () => setStep(0)
    window.addEventListener('wx:tour-start', again)
    return () => { alive = false; window.removeEventListener('wx:tour-start', again) }
  }, [user])

  // each step: open its screen, find its element (waiting a little for it), scroll to it
  useEffect(() => {
    if (step < 0 || step >= STEPS.length) { els.current = []; setRect(null); return }
    const me = ++run.current
    const s = STEPS[step]
    setMissing(false)
    els.current = []; setRect(null)           // never the previous step's highlight while this one is found
    ;(async () => {
      try { if (s.go) await s.go({ step, warehouse }) } catch { /* the screen could not be opened: explained below */ }
      if (!s.target) { if (me === run.current) { els.current = []; setRect(null) } return }
      let found = []
      for (let i = 0; i < 20 && me === run.current; i++) {       // up to ~3 s for data to load
        found = s.target()
        if (found.length) break
        await sleep(150)
      }
      if (me !== run.current) return
      els.current = found
      if (!found.length) { setMissing(true); setRect(null); return }
      found[0].scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' })
      await sleep(60)
      if (me === run.current) setRect(unionRect(found))
    })()
  }, [step, warehouse])

  // keep the highlight on its element as the page scrolls, resizes or loads more
  useEffect(() => {
    if (step < 0 || step >= STEPS.length) return
    const follow = () => { if (els.current.length) setRect(unionRect(els.current.filter((e) => e.isConnected))) }
    const id = setInterval(follow, 300)
    window.addEventListener('resize', follow)
    window.addEventListener('scroll', follow, true)
    return () => { clearInterval(id); window.removeEventListener('resize', follow); window.removeEventListener('scroll', follow, true) }
  }, [step])

  useLayoutEffect(() => {
    if (!box.current) return
    setPos(place(rect, box.current.getBoundingClientRect(), window.innerWidth, window.innerHeight))
  }, [rect, step, missing])

  // focus the tooltip on every step, so keyboard and screen-reader users follow it
  useEffect(() => { if (step >= 0) box.current?.querySelector('[data-tour-primary]')?.focus({ preventScroll: true }) }, [step, pos === null])

  const close = useCallback((how) => {
    run.current++
    if (how) tourState.set(user, how)
    setStep(-1); setRect(null); setMissing(false)
  }, [user])
  const next = useCallback(() => setStep((s) => {
    if (s + 1 >= STEPS.length) { tourState.set(user, 'completed'); return STEPS.length }
    return s + 1
  }), [user])
  const back = useCallback(() => setStep((s) => Math.max(0, s - 1)), [])

  useEffect(() => {
    if (step < 0) return
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); close(step >= STEPS.length ? 'completed' : 'skipped') }
      else if (step < STEPS.length && e.key === 'ArrowRight') next()
      else if (step < STEPS.length && e.key === 'ArrowLeft') back()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [step, close, next, back])

  if (step < 0) return null

  if (step >= STEPS.length) {
    return (
      <div className="wxtour-done" role="dialog" aria-modal="true" aria-labelledby="wxtour-done-title">
        <div className="wxtour-card wxtour-card--done" ref={box}>
          <span className="wxtour-check" aria-hidden="true">✓</span>
          <h2 id="wxtour-done-title">You’re all set!</h2>
          <p>You now have the freedom to explore the platform and try its features at your own pace.
            You can take the tour again any time from “Product tour” in the banner below.</p>
          <button type="button" className="wxtour-btn primary" data-tour-primary onClick={() => close('completed')}>Start exploring</button>
        </div>
      </div>
    )
  }

  const s = STEPS[step]
  const docked = !pos
  return (
    <>
      {rect && <div className="wxtour-spot" aria-hidden="true"
        style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }} />}
      {!rect && <div className="wxtour-dim" aria-hidden="true" />}
      <div ref={box} className={'wxtour-card' + (docked ? ' docked' : '')} style={pos || undefined}
        role="dialog" aria-modal="false" aria-labelledby="wxtour-title" aria-describedby="wxtour-body">
        <div className="wxtour-head">
          <span className="wxtour-count" aria-live="polite">Step {step + 1} of {STEPS.length}</span>
          <button type="button" className="wxtour-x" aria-label="Close the tour" onClick={() => close('skipped')}>×</button>
        </div>
        <h2 id="wxtour-title">{s.title}</h2>
        <p id="wxtour-body">{s.body}</p>
        {missing && <p className="wxtour-note">This part of WAREXX isn’t on the screen right now{window.innerWidth < 900 ? ' — open the menu (☰) to find it' : ''}. You can carry on with the tour.</p>}
        <div className="wxtour-bar" aria-hidden="true"><i style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} /></div>
        <div className="wxtour-foot">
          <button type="button" className="wxtour-skip" onClick={() => close('skipped')}>Skip tour</button>
          <span className="wxtour-nav">
            {step > 0 && <button type="button" className="wxtour-btn" onClick={back}>Back</button>}
            <button type="button" className="wxtour-btn primary" data-tour-primary onClick={next}>
              {step + 1 === STEPS.length ? 'Finish tour' : 'Next'}</button>
          </span>
        </div>
      </div>
    </>
  )
}
