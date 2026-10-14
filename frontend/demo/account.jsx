// The customer's own account inside WAREXX: My profile → "Subscription & billing".
//
// The WAREXX app (vendor/warexx-app) renders window.WAREXX_ACCOUNT_PANEL in its
// My profile window when a host page provides one; main.jsx provides this.
// Everything here talks to /api/subscribe (lead-api/payments.py) with the
// customer's own session pass — whose account it is comes from that pass on the
// server, never from this page.
//
//   paying customer   plan, status, period; renew / change plan (→ /pay);
//                     cancel (access runs to the end of the paid period) and
//                     undo; billing details; payments and printable receipts;
//                     sign-in password; sign out
//   free-trial user   when the trial ends; subscribe; sign out
//   plain demo        subscribe
import React, { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

const K = { session: 'wx_trial_session', account: 'wx_trial_account', ticket: 'wx_trial_ticket' }
const get = (k) => { try { return localStorage.getItem(K[k]) || '' } catch { return '' } }
const SUPPORT = 'warex.support@aavoraa.com'
const inr = (v) => '₹' + Number(v || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const day = (t) => (t ? new Date(typeof t === 'number' ? t * 1000 : t).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—')
const when = (t) => (t ? new Date(typeof t === 'number' ? t * 1000 : t).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }) : '—')
const COUNTRIES = ['India', 'United Arab Emirates', 'Saudi Arabia', 'Qatar', 'Kuwait', 'Bahrain', 'Oman', 'Singapore', 'Malaysia',
  'Sri Lanka', 'Nepal', 'Bangladesh', 'United States', 'United Kingdom', 'Canada', 'Australia', 'New Zealand', 'Germany',
  'France', 'Netherlands', 'South Africa', 'Kenya', 'Nigeria', 'Other']
const MSG = {
  signed_out: 'Your session has ended — sign out and sign in again.', too_many_attempts: 'Too many attempts — please wait a few minutes.',
  billing_unavailable: 'Billing is temporarily unavailable. Please try again shortly.', offline: 'Could not reach WAREXX. Check your connection.',
  wrong_password: 'Your current password is not right.', weak_password: 'Use at least 8 characters.',
  not_active: 'There is no active subscription to cancel.', not_cancelled: 'This subscription can no longer be kept — subscribe again instead.',
}

async function call(action, body = {}) {
  let r
  try {
    r = await fetch('/api/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, session: get('session'), ...body }) })
  } catch { return { detail: 'offline' } }
  let j = {}
  try { j = await r.json() } catch { /* empty */ }
  return r.ok ? { ok: true, ...j } : { status: r.status, ...j }
}

function signOut() {
  try { for (const k of Object.values(K)) localStorage.removeItem(k) } catch { /* private mode */ }
  location.href = location.pathname           // the demo opens on its sign-in again
}

const STATUS = {
  active: ['Active', 'ok'], grace: ['Grace period', 'warn'], payment_pending: ['Payment pending', 'warn'],
  payment_failed: ['Payment failed', 'bad'], expired: ['Expired', 'bad'], cancelled: ['Cancelled', 'bad'],
  suspended: ['Suspended', 'bad'], restricted: ['Restricted', 'warn'], trial: ['Trial', ''],
}
const ORDER = { paid: ['Paid', 'ok'], partially_refunded: ['Partly refunded', 'ok'], refunded: ['Refunded', ''], pending: ['Pending', 'warn'],
  failed: ['Failed', 'bad'], cancelled: ['Not completed', ''], expired: ['Expired', ''], unknown: ['Under review', 'warn'] }
const Pill = ({ s, map }) => { const [t, k] = map[s] || [s, '']; return <span className={'wxacc-pill ' + k}>{t}</span> }

export function AccountPanel({ toast }) {
  const say = (m, kind) => (toast ? toast(m, kind) : null)
  const [info, setInfo] = useState(null)
  const [err, setErr] = useState('')
  const load = async () => {
    if (!get('session')) { setInfo({ kind: 'demo' }); return }
    const r = await call('account_info')
    if (r.ok) { setInfo(r); setErr('') } else setErr(MSG[r.detail] || 'Could not load your subscription.')
  }
  useEffect(() => { load() }, [])

  if (err) return <section className="wxacc" id="prof-billing"><div className="prof-section">Subscription &amp; billing</div>
    <div className="prof-err" role="alert">{err}</div>
    <div className="prof-actions"><button type="button" className="btn" onClick={load}>Try again</button>
      <button type="button" className="btn" onClick={signOut}>Sign out</button></div></section>
  if (!info) return <section className="wxacc" id="prof-billing"><div className="prof-section">Subscription &amp; billing</div>
    <div className="small" style={{ color: 'var(--text-2)' }}>Loading…</div></section>
  if (info.kind === 'demo') return (
    <section className="wxacc" id="prof-billing">
      <div className="prof-section">Subscription</div>
      <p className="wxacc-note">You’re exploring the WAREXX demo with a sample business. Subscribe to run WAREXX for your own business.</p>
      <div className="prof-actions"><a className="btn primary" href="/pay/?plan=monthly">Subscribe</a></div>
    </section>)
  if (info.kind === 'trial') return (
    <section className="wxacc" id="prof-billing">
      <div className="prof-section">Free trial</div>
      <dl className="prof-facts">
        <div><dt>Signed in as</dt><dd>{info.email}</dd></div>
        <div><dt>Trial ends</dt><dd>{when(info.trial_end)}</dd></div>
        <div><dt>Plan</dt><dd>Free trial</dd></div>
      </dl>
      <p className="wxacc-note">Liked what you saw? Subscribe to keep using WAREXX for your business once the trial ends.</p>
      <div className="prof-actions">
        <a className="btn primary" href="/pay/?plan=monthly">Subscribe monthly</a>
        <a className="btn" href="/pay/?plan=annual">Subscribe annually</a>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn" onClick={signOut}>Sign out</button>
      </div>
    </section>)
  return <Subscriber info={info} reload={load} say={say} />
}

function Subscriber({ info, reload, say }) {
  const s = info.subscription || {}
  const other = s.plan === 'annual' ? 'monthly' : 'annual'
  const live = s.status === 'active' || s.status === 'grace'
  const [confirm, setConfirm] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState('')
  const [receipt, setReceipt] = useState(null)

  const cancel = async () => {
    setBusy('cancel')
    const r = await call('cancel_subscription', { reason })
    setBusy('')
    if (!r.ok) return say(MSG[r.detail] || 'Could not cancel. Please try again.', 'err')
    setConfirm(false); say(`Subscription cancelled — you keep access until ${day(r.ends_at)}.`, 'ok'); reload()
  }
  const keep = async () => {
    setBusy('keep')
    const r = await call('resume_subscription')
    setBusy('')
    if (!r.ok) return say(MSG[r.detail] || 'Could not undo the cancellation.', 'err')
    say('✓ Your subscription continues.', 'ok'); reload()
  }
  const openReceipt = async (ref) => {
    const r = await call('receipt', { order_ref: ref })
    if (!r.ok) return say(MSG[r.detail] || 'Could not open the receipt.', 'err')
    setReceipt(r.receipt)
  }

  return (
    <section className="wxacc" id="prof-billing">
      <div className="prof-section">Subscription &amp; billing</div>
      <dl className="prof-facts">
        <div><dt>Plan</dt><dd>{s.plan_name || '—'}</dd></div>
        <div><dt>Status</dt><dd>{s.cancel_at_period_end && live ? <span className="wxacc-pill warn">Ends {day(s.period_end)}</span> : <Pill s={s.status} map={STATUS} />}</dd></div>
        <div><dt>{live ? (s.cancel_at_period_end ? 'Access until' : 'Paid until') : 'Ended'}</dt><dd>{day(s.grace_until && s.status === 'grace' ? s.grace_until : s.period_end)}</dd></div>
        <div><dt>Current period</dt><dd>{s.period_start ? `${day(s.period_start)} – ${day(s.period_end)}` : '—'}</dd></div>
        <div><dt>Workspace</dt><dd>{info.workspace}</dd></div>
        <div><dt>Account email</dt><dd>{info.email}</dd></div>
      </dl>

      {s.cancel_at_period_end && live && (
        <div className="wxacc-box warn">
          <div><b>Your subscription is cancelled.</b> You keep full access until {day(s.period_end)}; nothing more will be charged.</div>
          <button type="button" className="btn primary" disabled={busy === 'keep'} onClick={keep}>{busy === 'keep' ? 'Please wait…' : 'Keep my subscription'}</button>
        </div>
      )}
      {!live && (
        <div className="wxacc-box bad"><div><b>Your subscription isn’t active.</b> Subscribe again to keep using WAREXX for your business.</div>
          <a className="btn primary" href={`/pay/?plan=${s.plan || 'monthly'}`}>Subscribe again</a></div>
      )}
      {s.status === 'grace' && (
        <div className="wxacc-box warn"><div><b>Your paid period has ended.</b> Renew before {day(s.grace_until)} to keep access.</div>
          <a className="btn primary" href={`/pay/?plan=${s.plan}`}>Renew now</a></div>
      )}

      {live && (
        <div className="prof-actions wxacc-acts">
          <a className="btn primary" href={`/pay/?plan=${s.plan}`}>{s.cancel_at_period_end ? 'Pay for another period' : 'Renew early'}</a>
          <a className="btn" href={`/pay/?plan=${other}`}>Switch to {info.plans?.[other]?.name || other}</a>
          <div style={{ flex: 1 }} />
          {!s.cancel_at_period_end && !confirm && <button type="button" className="btn wxacc-danger" onClick={() => setConfirm(true)}>Cancel subscription</button>}
        </div>
      )}
      {live && <p className="wxacc-note">Payments are made in advance for each period through HDFC Bank SmartGateway; nothing is charged automatically.
        A renewal or a plan change starts when your current period ends.</p>}

      {confirm && (
        <div className="wxacc-box bad" role="alertdialog" aria-label="Cancel subscription">
          <div>
            <b>Cancel your WAREXX subscription?</b>
            <p>You keep full access until <b>{day(s.period_end)}</b>, then your workspace is closed to sign-ins. Payments already made are
              not refunded automatically — see the <a href="/legal/#refunds" target="_blank" rel="noopener">Refund &amp; Cancellation Policy</a>,
              or write to <a href={`mailto:${SUPPORT}`}>{SUPPORT}</a>. You can undo this any time before {day(s.period_end)}.</p>
            <div className="field"><label>Reason (optional)</label>
              <select value={reason} onChange={(e) => setReason(e.target.value)}>
                <option value="">Choose a reason</option>
                <option>Too expensive</option><option>Missing a feature I need</option><option>Switching to another product</option>
                <option>Only needed it for a short time</option><option>Business closed or paused</option><option>Other</option>
              </select></div>
          </div>
          <div className="wxacc-row">
            <button type="button" className="btn" onClick={() => setConfirm(false)}>Keep subscription</button>
            <button type="button" className="btn wxacc-danger solid" disabled={busy === 'cancel'} onClick={cancel}>{busy === 'cancel' ? 'Cancelling…' : 'Yes, cancel'}</button>
          </div>
        </div>
      )}

      <Details info={info} say={say} reload={reload} />

      <div className="prof-section">Payments &amp; receipts</div>
      {(info.orders || []).length ? (
        <div className="wxacc-table"><table>
          <thead><tr><th>Date</th><th>Plan</th><th>Amount</th><th>Status</th><th>Receipt</th></tr></thead>
          <tbody>{info.orders.map((o) => (
            <tr key={o.order_ref}>
              <td>{day(o.paid_at || o.created_at)}</td>
              <td>{o.plan_name}{o.kind === 'renewal' ? ' · renewal' : ''}</td>
              <td>{inr(o.amount)}{o.environment === 'sandbox' && <small> (test)</small>}</td>
              <td><Pill s={o.status} map={ORDER} /></td>
              <td>{o.invoice_no ? <button type="button" className="btn wxacc-link" onClick={() => openReceipt(o.order_ref)}>{o.invoice_no}</button> : '—'}</td>
            </tr>))}</tbody>
        </table></div>
      ) : <div className="small" style={{ color: 'var(--text-2)' }}>No payments yet.</div>}

      <Security say={say} />

      <div className="prof-actions wxacc-acts">
        <span className="wxacc-note">Need help with billing? <a href={`mailto:${SUPPORT}`}>{SUPPORT}</a></span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn" onClick={signOut}>Sign out of WAREXX</button>
      </div>
      {receipt && <Receipt r={receipt} onClose={() => setReceipt(null)} />}
    </section>
  )
}

function Details({ info, say, reload }) {
  const [f, setF] = useState(info.details)
  const [errs, setErrs] = useState({})
  const [busy, setBusy] = useState(false)
  const dirty = Object.keys(info.details).some((k) => (f[k] || '') !== (info.details[k] || ''))
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }))
  const save = async (e) => {
    e.preventDefault(); setBusy(true)
    const r = await call('update_details', f)
    setBusy(false)
    if (!r.ok) { setErrs(r.fields || {}); return say(r.fields ? 'Please check the highlighted details.' : (MSG[r.detail] || 'Could not save.'), 'err') }
    setErrs({}); say('✓ Billing details saved', 'ok'); reload()
  }
  const field = (k, label, props = {}) => (
    <div className={'field' + (props.wide ? ' wxacc-wide' : '')}><label>{label}</label>
      <input value={f[k] || ''} onChange={set(k)} aria-invalid={errs[k] ? 'true' : 'false'} {...props.input} />
      {errs[k] && <small className="prof-err">{errs[k] === 'required' ? 'Required' : 'Not valid'}</small>}</div>)
  return (
    <form onSubmit={save}>
      <div className="prof-section">Billing details</div>
      <div className="prof-grid">
        {field('name', 'Full name', { input: { maxLength: 80, autoComplete: 'name' } })}
        {field('phone', 'Mobile number', { input: { maxLength: 20, type: 'tel', autoComplete: 'tel' } })}
        {field('company', 'Company', { input: { maxLength: 120, autoComplete: 'organization' } })}
        {field('gstin', 'GSTIN (optional)', { input: { maxLength: 15 } })}
        {field('address', 'Billing address', { wide: true, input: { maxLength: 200, autoComplete: 'street-address' } })}
        {field('city', 'City', { input: { maxLength: 80 } })}
        {field('state', 'State / Region', { input: { maxLength: 80 } })}
        {field('postcode', 'PIN / Postal code', { input: { maxLength: 12 } })}
        <div className="field"><label>Country</label>
          <select value={f.country || ''} onChange={set('country')}>{COUNTRIES.map((c) => <option key={c}>{c}</option>)}</select></div>
      </div>
      <div className="prof-actions">
        <span className="wxacc-note">Used on your next payment and receipt. Past receipts keep the details they were issued with.</span>
        <div style={{ flex: 1 }} />
        <button className="btn primary" type="submit" disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save billing details'}</button>
      </div>
    </form>
  )
}

function Security({ say }) {
  const [f, setF] = useState({ current: '', password: '', again: '' })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }))
  const save = async (e) => {
    e.preventDefault()
    if (f.password.length < 8) return setErr('Use at least 8 characters.')
    if (f.password !== f.again) return setErr('The two new passwords do not match.')
    setErr(''); setBusy(true)
    const r = await call('change_password', { current: f.current, password: f.password })
    setBusy(false)
    if (!r.ok) return setErr(MSG[r.detail] || 'Could not change the password.')
    setF({ current: '', password: '', again: '' }); say('✓ Password changed — use it the next time you sign in', 'ok')
  }
  return (
    <form onSubmit={save}>
      <div className="prof-section">Sign-in password</div>
      <div className="prof-grid">
        <div className="field wxacc-wide"><label>Current password</label>
          <input type="password" value={f.current} onChange={set('current')} autoComplete="current-password" required /></div>
        <div className="field"><label>New password</label>
          <input type="password" value={f.password} onChange={set('password')} autoComplete="new-password" minLength={8} required /></div>
        <div className="field"><label>Confirm new password</label>
          <input type="password" value={f.again} onChange={set('again')} autoComplete="new-password" required /></div>
      </div>
      {err && <div className="prof-err" role="alert">{err}</div>}
      <div className="prof-actions"><div style={{ flex: 1 }} />
        <button className="btn" type="submit" disabled={busy || !f.current || !f.password}>{busy ? 'Saving…' : 'Change password'}</button></div>
    </form>
  )
}

function Receipt({ r, onClose }) {
  useEffect(() => {
    document.body.classList.add('wxacc-printable')
    const esc = (e) => { if (e.key === 'Escape') { e.stopImmediatePropagation(); onClose() } }
    window.addEventListener('keydown', esc, true)
    return () => { document.body.classList.remove('wxacc-printable'); window.removeEventListener('keydown', esc, true) }
  }, [onClose])
  const rows = [['Receipt no.', r.invoice_no], ['Date', day(r.issued_at)], ['Order', r.order_ref], ['Billed to', r.company],
    ['', r.bill_to], ['GSTIN', r.gstin || '—'], ['Customer', `${r.customer} · ${r.email}`],
    ['Plan', `WAREXX ${r.plan_name} — ${day(r.period_start)} to ${day(r.period_end)}`]]
  return createPortal(
    <div className="wxacc-rcpt-scrim" onClick={onClose}>
      <div className="wxacc-rcpt" role="dialog" aria-modal="true" aria-label={'Receipt ' + r.invoice_no} onClick={(e) => e.stopPropagation()}>
        <div className="wxacc-rcpt-head"><b>WAREXX</b><span>Payment receipt</span></div>
        {r.environment === 'sandbox' && <p className="wxacc-rcpt-test">Test payment (HDFC SmartGateway sandbox) — not a real charge.</p>}
        <table className="wxacc-rcpt-facts"><tbody>{rows.map(([k, v], i) => <tr key={i}><th>{k}</th><td>{v}</td></tr>)}</tbody></table>
        <table className="wxacc-rcpt-sum"><tbody>
          <tr><th>Subscription ({r.plan_name})</th><td>{inr(r.base)}</td></tr>
          <tr><th>GST ({Number(r.tax_percent)}%)</th><td>{inr(r.tax)}</td></tr>
          <tr className="tot"><th>Total</th><td>{inr(r.total)}</td></tr>
          <tr><th>Paid via HDFC SmartGateway{r.payment_method ? ` · ${r.payment_method}` : ''}</th><td>{inr(r.amount_charged)}</td></tr>
          {Number(r.amount_refunded) > 0 && <tr><th>Refunded</th><td>−{inr(r.amount_refunded)}</td></tr>}
        </tbody></table>
        {r.txn_id && <p className="wxacc-rcpt-small">Transaction {r.txn_id}</p>}
        <p className="wxacc-rcpt-small">Questions about this payment: {SUPPORT}</p>
        <div className="wxacc-row wxacc-noprint">
          <button type="button" className="btn" onClick={onClose}>Close</button>
          <button type="button" className="btn primary" onClick={() => window.print()}>Print / save as PDF</button>
        </div>
      </div>
    </div>, document.body)
}
