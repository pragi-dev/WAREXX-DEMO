// ---------------------------------------------------------------------------
//  The demo sandbox, browser side (see docs/DEMO_SANDBOX.md)
//  --------------------------------------------------------------------------
//  Two pieces, both mounted by main.jsx and neither touching the app itself:
//
//    DemoLogin   the page at /d/<slug> — every state of a booked demo, from
//                "setting up your workspace" to "your demo has ended"
//    DemoChrome  rides alongside the app during a demo: the countdown in the
//                last 15 minutes, and the full-screen "demo has ended" view
//
//  Only a demo session ever renders DemoChrome — main.jsx checks for one — so a
//  client deployment never sees any of this.
// ---------------------------------------------------------------------------
import React, { useEffect, useRef, useState } from 'react'
import { session } from './api.js'

import { BrandMark, Wordmark } from './brand.jsx'

const DEMO_KEY = 'warexx_demo'

export const demoSession = {
  get: () => {
    try { return JSON.parse(localStorage.getItem(DEMO_KEY) || 'null') } catch { return null }
  },
  set: (d) => { try { localStorage.setItem(DEMO_KEY, JSON.stringify(d)) } catch { /* private mode */ } },
  clear: () => { try { localStorage.removeItem(DEMO_KEY) } catch { /* private mode */ } },
}

const fmtLeft = (ms) => {
  if (ms <= 0) return '0:00'
  const s = Math.floor(ms / 1000)
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}:${String(sec).padStart(2, '0')}`
}

const fmtUntil = (ms) => {
  const m = Math.max(0, Math.round(ms / 60000))
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h} h ${m % 60} min`
  return `${Math.round(h / 24)} days`
}

const readJson = async (r) => { try { return await r.json() } catch { return {} } }

// ===========================================================================
//  /d/<slug>
// ===========================================================================
export function DemoLogin({ slug }) {
  const [st, setSt] = useState(null)          // the status answer
  const [missing, setMissing] = useState(false)
  const [skew, setSkew] = useState(0)          // server clock minus ours
  const [tick, setTick] = useState(0)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [resent, setResent] = useState('')
  const typed = useRef(false)

  // Poll while the worker is building, slowly otherwise: the window opening and
  // closing is worked out from the clock between polls.
  useEffect(() => {
    let alive = true, timer
    const load = async () => {
      try {
        const r = await fetch(`/api/demo/requests/${encodeURIComponent(slug)}/status`)
        if (r.status === 404) { if (alive) setMissing(true); return }
        const j = await readJson(r)
        if (!alive || !r.ok) return
        setSt(j)
        if (j.now) setSkew(Date.parse(j.now) - Date.now())
        if (j.username && !typed.current) setUsername(j.username)
        const building = j.status === 'requested' || j.status === 'provisioning'
        timer = setTimeout(load, building ? 3000 : 30000)
      } catch {
        timer = setTimeout(load, 5000)
      }
    }
    load()
    return () => { alive = false; clearTimeout(timer) }
  }, [slug])

  useEffect(() => { const t = setInterval(() => setTick((n) => n + 1), 1000); return () => clearInterval(t) }, [])

  const now = Date.now() + skew
  let phase = st?.status
  if (st && (phase === 'not_started' || phase === 'open')) {
    // the clock moves between polls; the page should not wait 30 s to open
    if (now >= Date.parse(st.ends_at)) phase = 'expired'
    else if (now >= Date.parse(st.opens_at)) phase = 'open'
    else phase = 'not_started'
  }

  const submit = async (e) => {
    e.preventDefault(); setErr(''); setBusy(true)
    try {
      const r = await fetch('/api/demo/login', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ slug, username: username.trim(), password }),
      })
      const j = await readJson(r)
      if (!r.ok) {
        setErr(j.message || (typeof j.detail === 'string' ? j.detail : '') || 'Sign-in failed')
        setBusy(false)
        return
      }
      demoSession.set({ slug, ends_at: j.demo?.ends_at, company: j.demo?.company || '',
        book_call_url: j.demo?.book_call_url || '', whatsapp_url: j.demo?.whatsapp_url || '' })
      session.set(j.token)
      // a full load: the app reads its session once, at start
      window.location.replace('/')
    } catch {
      setErr('Could not reach the server. Check your connection and try again.')
      setBusy(false)
    }
  }

  const resend = async () => {
    setResent('sending')
    const r = await fetch(`/api/demo/requests/${encodeURIComponent(slug)}/resend`, { method: 'POST' })
    const j = await readJson(r)
    setResent(r.ok ? 'sent' : (typeof j.detail === 'string' ? j.detail : 'Could not send — try again shortly.'))
  }

  const bookCall = st?.book_call_url
  const whatsapp = st?.whatsapp_url
  const name = st?.first_name ? `, ${st.first_name}` : ''
  // DEMO_SAMPLE_DATA: off means every demo starts empty
  const sample = st?.sample_data !== false

  let card
  if (missing) {
    card = (<>
      <h2 className="login-title">Demo link not found</h2>
      <p className="login-sub">Check that the link matches the one in your email, or book a new demo.</p>
    </>)
  } else if (!st) {
    card = <Spinner text="Loading your demo…" />
  } else if (phase === 'requested' || phase === 'provisioning') {
    card = (<>
      <h2 className="login-title">Setting up your workspace{name}</h2>
      <p className="login-sub">We are building a private copy of WAREXX for {st.company || 'you'}
        {sample ? ', with sample warehouses, suppliers and stock' : ''}. This takes under a minute.</p>
      <Spinner text={phase === 'provisioning' ? (sample ? 'Copying sample data…' : 'Preparing your workspace…') : 'Queued…'} />
      <p className="login-foot">Your login details will arrive by email as soon as it is ready.</p>
    </>)
  } else if (phase === 'failed') {
    card = (<>
      <h2 className="login-title">We could not set up your workspace</h2>
      <p className="login-sub">Something went wrong on our side. Our team has been alerted and will
        contact you shortly.</p>
      {bookCall && <a className="login-submit demo-link-btn" href={bookCall}>Book a call instead</a>}
    </>)
  } else if (phase === 'not_started') {
    card = (<>
      <h2 className="login-title">Your workspace is ready{name}</h2>
      <p className="login-sub">Your demo opens <b>{st.window}</b>. Your username and password are in
        your email.</p>
      <div className="demo-countdown" aria-live="polite">
        <span>Opens in</span><b>{fmtUntil(Date.parse(st.opens_at) - now)}</b>
      </div>
      <ResendRow state={resent} onResend={resend} />
    </>)
  } else if (phase === 'open') {
    card = (
      <form onSubmit={submit} noValidate>
        <h2 className="login-title">Your WAREXX demo{name}</h2>
        <p className="login-sub">Open until {st.window.split('–')[1]?.trim() || st.window}. Sign in with the
          details from your email.</p>
        <label className="login-field">
          <span>Username</span>
          <div className="login-input">
            <input value={username} onChange={(e) => { typed.current = true; setUsername(e.target.value) }}
              autoComplete="username" spellCheck={false} placeholder="Your username" />
          </div>
        </label>
        <label className="login-field">
          <span>Password</span>
          <div className="login-input">
            <input type={show ? 'text' : 'password'} value={password} autoFocus
              onChange={(e) => setPassword(e.target.value)} autoComplete="current-password"
              placeholder="From your email" />
            <button type="button" className="login-eye" onClick={() => setShow((v) => !v)}
              aria-label={show ? 'Hide password' : 'Show password'}>{show ? 'Hide' : 'Show'}</button>
          </div>
        </label>
        {err && <div className="login-err" role="alert">{err}</div>}
        <button className="login-submit" disabled={busy || !username.trim() || !password}>
          {busy && <span className="login-spin" aria-hidden="true" />}
          {busy ? 'Signing in…' : 'Open my demo'}
        </button>
        <ResendRow state={resent} onResend={resend} />
      </form>
    )
  } else if (phase === 'expired') {
    card = (<>
      <h2 className="login-title">Your demo has ended</h2>
      <p className="login-sub">Thanks for trying WAREXX{name}. Want to see it set up for your own
        business? We would be glad to walk you through it.</p>
      <CallButtons bookCall={bookCall} whatsapp={whatsapp} />
    </>)
  } else {
    card = (<>
      <h2 className="login-title">This demo has closed</h2>
      <p className="login-sub">Its data has been removed. Book a new demo, or talk to us
        directly.</p>
      <CallButtons bookCall={bookCall} whatsapp={whatsapp} />
    </>)
  }

  return (
    <div className="login-wrap">
      <aside className="login-side" aria-hidden="true">
        <div className="login-side-brand">
          <BrandMark className="login-mark"/>
          <span className="login-side-name"><Wordmark tagline /><em>Live demo</em></span>
        </div>
        <div className="login-side-copy">
          <h1>Your own private WAREXX, for the length of your demo.</h1>
          <p>{sample
            ? 'Sample warehouses, suppliers and stock are already loaded. Read an invoice, receive goods, move stock'
            : 'It starts empty, ready for your own suppliers, warehouses and stock'} — nothing you do touches anyone else's data.</p>
          <ul>
            <li><b>Warehouse</b><span>GRN, stock, audits, dispatch</span></li>
            <li><b>Invoices</b><span>Read by AI, checked by you</span></li>
            <li><b>Reports</b><span>Every register, filtered and exportable</span></li>
          </ul>
        </div>
        <div className="login-side-foot">© {new Date().getFullYear()} WAREXX by Aavoraa · Enterprise ERP</div>
      </aside>
      <main className="login-main">
        <div className="login-card demo-card" data-tick={tick % 2}>
          <div className="login-card-mark"><BrandMark className="login-mark" onLight /><Wordmark onLight /></div>
          {card}
        </div>
      </main>
    </div>
  )
}

function Spinner({ text }) {
  return (
    <div className="demo-wait" role="status">
      <span className="demo-spin" aria-hidden="true" /><span>{text}</span>
    </div>
  )
}

function ResendRow({ state, onResend }) {
  if (state === 'sent') return <p className="login-foot">A new password is on its way. The old one no longer works.</p>
  if (state === 'sending') return <p className="login-foot">Sending…</p>
  return (
    <p className="login-foot">
      {state && <>{state} · </>}
      Didn&apos;t get the email? <button type="button" className="demo-textbtn" onClick={onResend}>Send me a new password</button>
    </p>
  )
}

function CallButtons({ bookCall, whatsapp }) {
  return (
    <div className="demo-actions">
      {bookCall && <a className="login-submit demo-link-btn" href={bookCall}>Book a call</a>}
      {whatsapp && <a className="demo-ghost-btn" href={whatsapp}>WhatsApp us</a>}
    </div>
  )
}

// ===========================================================================
//  Alongside the app, during a demo
// ===========================================================================
export function DemoChrome() {
  const [demo, setDemo] = useState(() => demoSession.get())
  const [ended, setEnded] = useState(false)
  const endedRef = useRef(false)
  const [, setTick] = useState(0)

  useEffect(() => {
    // the server's end time rides on every response (api.js forwards it), so an
    // extension by sales reaches the banner without a reload
    const onEnds = (e) => setDemo((d) => {
      if (!d || d.ends_at === e.detail) return d
      const next = { ...d, ends_at: e.detail }
      demoSession.set(next)
      return next
    })
    const onEnded = () => { endedRef.current = true; setEnded(true) }
    window.addEventListener('warexx:demo-ends', onEnds)
    window.addEventListener('warexx:demo-ended', onEnded)
    const t = setInterval(() => {
      setTick((n) => n + 1)
      // signed out (the app cleared the token): back to this demo's own page,
      // since the ordinary login screen does not exist on the demo host. Not
      // once the demo is over — the ended view below is already the last word,
      // and jumping away from it a second later reads as a glitch.
      const d = demoSession.get()
      if (!session.get() && d?.slug && !endedRef.current && Date.parse(d.ends_at) > Date.now())
        window.location.replace(`/d/${d.slug}`)
    }, 1000)
    return () => {
      window.removeEventListener('warexx:demo-ends', onEnds)
      window.removeEventListener('warexx:demo-ended', onEnded)
      clearInterval(t)
    }
  }, [])

  if (!demo) return null
  const left = Date.parse(demo.ends_at) - Date.now()
  const call = demo.book_call_url || `/d/${demo.slug}`
  if (ended || left <= 0) {
    return (
      <div className="demo-ended" role="dialog" aria-modal="true" aria-labelledby="demo-ended-title">
        <div className="demo-ended-card">
          <BrandMark className="login-mark" onLight />
          <h2 id="demo-ended-title">Your demo has ended</h2>
          <p>Thanks for trying WAREXX{demo.company ? ` with ${demo.company}` : ''}. Want to see it set up
            for your own business? We would be glad to walk you through it.</p>
          <div className="demo-actions">
            <a className="login-submit demo-link-btn" href={call}>Book a call</a>
            {demo.whatsapp_url && <a className="demo-ghost-btn" href={demo.whatsapp_url}>WhatsApp us</a>}
          </div>
        </div>
      </div>
    )
  }
  if (left > 15 * 60 * 1000) return null
  return (
    <div className="demo-banner" role="status" aria-live="polite">
      <span>Demo ends in <b>{fmtLeft(left)}</b></span>
      <a href={call} target="_blank" rel="noopener">Book a call</a>
    </div>
  )
}
