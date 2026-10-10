// The demo's sign-up / sign-in, for a slot booked on the landing page.
//
//   landing contact form (with a slot) ──► /demo/#t=<ticket>
//        ──► SIGN UP: email filled in, choose a password
//        ──► the demo, for two hours from the start of the slot
//        ──► then the password stops working and a "subscribe" card covers it
//
// The passes (ticket, account, session) are signed by the server
// (lead-api/trial.py) and checked there, against its clock, on every visit and
// every few minutes during one — so moving the window here changes nothing.
// On only when built with VITE_DEMO_TRIAL=1 (npm run build:site); the stand-alone
// demo and the phone apps' bundled demo stay open.
import React, { useEffect, useRef, useState } from 'react'
import { BrandMark, Wordmark } from '../vendor/warexx-app/src/brand.jsx'

export const TRIAL_ON = import.meta.env.VITE_DEMO_TRIAL === '1'
const LANDING = import.meta.env.VITE_LANDING_URL || '/'
const K = { ticket: 'wx_trial_ticket', account: 'wx_trial_account', session: 'wx_trial_session' }
const store = {
  get: (k) => { try { return localStorage.getItem(K[k]) || '' } catch { return '' } },
  set: (k, v) => { try { v ? localStorage.setItem(K[k], v) : localStorage.removeItem(K[k]) } catch { /* private mode */ } },
}

/** The guided tour finished or skipped: kept against this trial on the server
 *  (lead-api/store.py), so it is not offered again on another device. */
export function reportTour(state) {
  const session = store.get('session')
  if (TRIAL_ON && session) call('tour', { session, state })
}

/** The signed-in trial user's email, or '' (the demo without sign-up). */
export function trialUser() { return (TRIAL_ON && peek(store.get('session'))?.e) || '' }

/** The readable half of a pass (the server checks the signature, not this). */
export function peek(pass) {
  try {
    const b = pass.split('.')[0].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(decodeURIComponent(escape(atob(b + '='.repeat((4 - (b.length % 4)) % 4)))))
  } catch { return null }
}

async function call(action, body) {
  let r
  try {
    r = await fetch('/api/trial', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...body }) })
  } catch { return { status: 0, detail: 'offline' } }
  let j = {}
  try { j = await r.json() } catch { /* empty */ }
  return r.ok ? { ok: true, ...j } : { status: r.status, ...j }
}

/** A ticket handed over in the address (#t=…) from the landing page: kept, and
 *  taken out of the address. A new booking replaces the old account. */
export function takeTicketFromUrl() {
  const m = location.hash.match(/[#&]t=([^&]+)/)
  if (!m) return
  const t = decodeURIComponent(m[1])
  if (t !== store.get('ticket')) { store.set('ticket', t); store.set('account', ''); store.set('session', '') }
  history.replaceState(null, '', location.pathname + location.search)
}

const fmtWhen = (sec) => new Date(sec * 1000).toLocaleString(undefined,
  { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
const left = (ms) => {
  const s = Math.max(0, Math.round(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${m}m` : m ? `${m}m ${s % 60}s` : `${s}s`
}

/** Checks the stored session with the server: { state: 'open'|'not_yet'|'expired'|'none'|'error', end, start } */
export async function checkSession() {
  const s = store.get('session')
  if (!s) {
    // no secret on the server = no sign-up there: the demo opens as it always did
    if (!store.get('ticket') && !store.get('account')) {
      const st = await call('status', {})
      if (st.ok && st.configured === false) return { state: 'unconfigured' }
    }
    return { state: 'none' }
  }
  const r = await call('verify', { session: s })
  if (r.ok) return { state: 'open', end: r.end, skew: r.now ? r.now * 1000 - Date.now() : 0, tourState: r.tour_state || null }
  if (r.detail === 'not_yet') return { state: 'not_yet', start: r.start, end: r.end, window: r.window }
  if (r.detail === 'expired') { store.set('session', ''); return { state: 'expired' } }
  if (r.status === 401) { store.set('session', ''); return { state: 'none' } }
  return { state: 'error', detail: r.detail }
}

function Shell({ children, wide }) {
  return (
    <div className="wxtrial-wrap">
      <div className={'wxtrial-card' + (wide ? ' wide' : '')}>
        <div className="wxtrial-brand"><BrandMark onLight /><Wordmark tagline onLight /></div>
        {children}
      </div>
    </div>
  )
}

/** Before the demo: sign up / sign in / wait for the slot / book one. Calls
 *  onOpen({ end, skew }) once there is a session for an open window. */
export function TrialGate({ onOpen, initial }) {
  const ticket = store.get('ticket'), account = store.get('account')
  const t = peek(account) || peek(ticket)
  const [mode, setMode] = useState(initial?.state === 'expired' ? 'expired'
    : initial?.state === 'not_yet' ? 'wait' : account ? 'login' : ticket ? 'signup' : 'none')
  const [wait, setWait] = useState(initial?.state === 'not_yet' ? initial : null)
  const [pw, setPw] = useState(''), [pw2, setPw2] = useState('')
  const [busy, setBusy] = useState(false), [msg, setMsg] = useState(initial?.state === 'error' ? 'Could not reach WAREXX. Check the connection and try again.' : '')
  const [now, setNow] = useState(Date.now())
  const email = t?.e || ''

  useEffect(() => { const i = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(i) }, [])
  // waiting for the slot: try again the moment it opens
  useEffect(() => {
    if (mode !== 'wait' || !wait) return
    const ms = wait.start * 1000 - Date.now() + 1500
    const id = setTimeout(async () => {
      const r = await checkSession()
      if (r.state === 'open') onOpen(r); else if (r.state === 'expired') setMode('expired')
    }, Math.max(1000, ms))
    return () => clearTimeout(id)
  }, [mode, wait])

  const after = (r) => {
    if (r.account) store.set('account', r.account)
    if (r.session) store.set('session', r.session)
    checkSession().then((c) => {
      if (c.state === 'open') onOpen(c)
      else if (c.state === 'not_yet') { setWait(c); setMode('wait') }
      else if (c.state === 'expired') setMode('expired')
      else setMsg('Something went wrong. Please try again.')
    })
  }
  const fail = (r) => {
    setBusy(false)
    if (r.detail === 'expired') { store.set('session', ''); setMode('expired'); return }
    setMsg({
      wrong_password: 'That password is not right.',
      weak_password: 'Use at least 8 characters.',
      invalid_pass: 'This sign-up link is no longer valid. Book a slot again on the website.',
      too_many_attempts: 'Too many attempts. Please wait a while and try again.',
      trial_not_configured: 'Demo sign-up is not available right now. Please contact us.',
      offline: 'Could not reach WAREXX. Check the connection and try again.',
    }[r.detail] || 'Something went wrong. Please try again.')
  }
  const signup = async (e) => {
    e.preventDefault(); setMsg('')
    if (pw.length < 8) return setMsg('Use at least 8 characters.')
    if (pw !== pw2) return setMsg('The two passwords do not match.')
    setBusy(true)
    const r = await call('signup', { ticket, password: pw })
    return r.ok ? after(r) : fail(r)
  }
  const login = async (e) => {
    e.preventDefault(); setMsg(''); setBusy(true)
    const r = await call('login', { account, email, password: pw })
    return r.ok ? after(r) : fail(r)
  }

  if (mode === 'expired') return <SubscribeCard standalone />
  const windowLine = t && <p className="wxtrial-slot"><b>Your demo slot</b>{fmtWhen(t.s)} – {new Date(t.x * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}</p>

  if (mode === 'wait') return (
    <Shell>
      <h1>Your demo opens soon</h1>
      <p className="wxtrial-sub">You’re signed up as <b>{email}</b>. WAREXX opens here by itself at the start of your slot.</p>
      {windowLine}
      <p className="wxtrial-count">Opens in <b>{left(wait.start * 1000 - now)}</b></p>
    </Shell>
  )

  if (mode === 'none') return (
    <Shell>
      <h1>Book your WAREXX demo</h1>
      <p className="wxtrial-sub">Pick a two-hour slot on our website, then create your password here to explore WAREXX with a sample business.</p>
      <a className="wxtrial-btn" href={LANDING.replace(/\/?$/, '/') + '#contact'}>Choose a slot</a>
    </Shell>
  )

  const isSignup = mode === 'signup'
  return (
    <Shell>
      <h1>{isSignup ? 'Create your demo account' : 'Sign in to your demo'}</h1>
      <p className="wxtrial-sub">{isSignup
        ? 'Choose a password. It opens WAREXX for the two hours of your slot.'
        : 'Your password works until the end of your slot.'}</p>
      {windowLine}
      <form className="wxtrial-form" onSubmit={isSignup ? signup : login}>
        <label><span>Email</span><input type="email" value={email} readOnly autoComplete="username" /></label>
        <label><span>Password</span><input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus
          autoComplete={isSignup ? 'new-password' : 'current-password'} minLength={isSignup ? 8 : undefined} required /></label>
        {isSignup && <label><span>Confirm password</span><input type="password" value={pw2} onChange={(e) => setPw2(e.target.value)}
          autoComplete="new-password" required /></label>}
        {msg && <p className="wxtrial-err" role="alert">{msg}</p>}
        <button className="wxtrial-btn" type="submit" disabled={busy}>{busy ? 'Please wait…' : isSignup ? 'Create account & open demo' : 'Sign in'}</button>
      </form>
      {!isSignup && ticket && <button className="wxtrial-link" type="button" onClick={() => { store.set('account', ''); setMode('signup'); setMsg('') }}>Set a new password</button>}
    </Shell>
  )
}

/** The card shown when the two hours are over. */
export function SubscribeCard({ standalone }) {
  const base = LANDING.replace(/\/?$/, '/')
  const card = (
    <div className="wxtrial-card wxtrial-sub-card" role="dialog" aria-modal="true" aria-labelledby="wxtrial-end">
      <div className="wxtrial-brand"><BrandMark onLight /><Wordmark tagline onLight /></div>
      <h1 id="wxtrial-end">Your demo time is over</h1>
      <p className="wxtrial-sub">Thanks for trying WAREXX. Your two-hour demo has ended and its password no longer works.
        Subscribe to run your own warehouses, stores and stock on WAREXX.</p>
      <a className="wxtrial-btn" href={base + '#pricing'}>Subscribe to WAREXX</a>
      <a className="wxtrial-btn ghost" href={base + '#contact'}>Talk to our team</a>
    </div>
  )
  return standalone ? <div className="wxtrial-wrap">{card}</div> : <div className="wxtrial-overlay">{card}</div>
}

/** Beside the running demo: a quiet countdown, a re-check with the server every
 *  few minutes and when the tab comes back, and the subscribe card at the end. */
export function TrialWatch({ end, skew = 0 }) {
  const [over, setOver] = useState(false)
  const [now, setNow] = useState(Date.now() + skew)
  const endMs = useRef(end * 1000)
  useEffect(() => {
    if (over) return
    const tick = setInterval(() => {
      const n = Date.now() + skew
      setNow(n)
      if (n >= endMs.current) setOver(true)
    }, 1000)
    const recheck = async () => {
      const r = await checkSession()
      if (r.state === 'expired' || r.state === 'none') setOver(true)
    }
    const every = setInterval(recheck, 5 * 60 * 1000)
    const vis = () => { if (document.visibilityState === 'visible') recheck() }
    document.addEventListener('visibilitychange', vis)
    return () => { clearInterval(tick); clearInterval(every); document.removeEventListener('visibilitychange', vis) }
  }, [over])
  useEffect(() => { if (over) { store.set('session', ''); document.body.classList.add('wxtrial-ended') } }, [over])
  if (over) return <SubscribeCard />
  const ms = endMs.current - now
  return <div className={'wxtrial-timer' + (ms < 10 * 60 * 1000 ? ' soon' : '')} role="status">Demo time left <b>{left(ms)}</b></div>
}
