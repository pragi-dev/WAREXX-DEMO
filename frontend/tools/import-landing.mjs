// Bring a new landing page from the designer into the site.
//
//   node tools/import-landing.mjs <path to warexx-landing.html> [path to assets/]
//
// The designer ships one self-contained page (warexx-landing.html: CSS, JS and
// images inlined) built to run on its own, with no server. This copies it to
// landing/index.html and adds back the few hooks that tie it to WAREXX — and
// nothing else, so the design stays exactly as delivered:
//
//   * forms are sent to the landing site's lead API ("/api/leads");
//   * "Sign in" (header and mobile menu) opens the WAREXX software (appUrl);
//   * "Get demo trial" asks for a demo time slot and then opens the demo's
//     sign-up for that slot, or the demo itself when no slot pass comes back;
//   * the "What would you like to improve?" answer is sent as the lead's
//     interest, which the lead email carries;
//   * the demo film's video is optional (see VIDEO below).
//
// Every edit is anchored on text in the designer's page. If a new version no
// longer contains an anchor, the import stops and names it rather than
// shipping a page with a hook silently missing.
//
// VIDEO: the film's recorded video (assets/warexx-demo.mp4, ~120 MB) is too
// large for git (GitHub refuses files over 100 MB). Pass the designer's assets/
// folder and the video is copied to landing/assets/ (git-ignored) for local
// builds; on a host, set VITE_DEMO_VIDEO_URL to where the video is hosted. With
// neither, the page plays its built-in animated film instead.
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const DEST = path.join(FRONTEND, 'landing', 'index.html')

function edit(html, anchor, replace, what) {
  const n = html.split(anchor).length - 1
  if (n !== 1) throw new Error(`import-landing: "${what}" — expected the anchor once, found it ${n} time(s):\n  ${anchor.slice(0, 120)}`)
  // a function, always: a replacement STRING would read "$$" in the inserted
  // code as "$", and the page's $$() helper would quietly become $()
  return html.replace(anchor, () => (typeof replace === 'function' ? replace(anchor) : replace))
}

export function merge(html) {
  // ---- configuration: the three addresses (filled in at build time by
  // tools/landing.mjs) and the demo slots the lead API accepts
  html = edit(html,
    `  formEndpoint: "",\n`,
    `  formEndpoint: "/api/leads",

  /* The three addresses above and below are filled in at build time from
     VITE_LEAD_ENDPOINT, VITE_DEMO_URL and VITE_APP_URL (landing-demo/.env.example,
     tools/landing.mjs). "/api/leads" is the landing site's own lead API
     (landing-demo/api/leads.py); it never touches the WAREXX database. */

  /* The interactive demo (landing-demo/frontend/demo — the WAREXX software with
     a sample business, no server or database behind it). "Get demo trial" sends
     the lead, then opens this URL. Production: "https://demo.warexx.aavoraa.com/". */
  demoAppUrl: "http://localhost:8002/",

  /* The WAREXX software itself, for "Sign in". A separate deployment (app/),
     e.g. "https://app.warexx.aavoraa.com/". */
  appUrl: "http://localhost:8000/",

  /* The payment page "Subscribe" (Monthly / Annual) opens, with ?plan=monthly or
     ?plan=annual added. Empty: Subscribe opens a plan enquiry instead. Filled in
     at build time from VITE_PAYMENT_URL. */
  paymentUrl: "",

  /* Demo time slots offered on the "Get demo trial" forms. Each opens the demo
     for two hours from its start, after the visitor signs up. Keep in step with
     the server (DEMO_SLOT_HOURS / DEMO_SLOT_TZ_MINUTES / DEMO_SLOT_DAYS in
     lead-api/trial.py), which refuses any other time. */
  demoSlots: { hours: [10, 12, 15, 17], tzOffsetMinutes: 330, tzLabel: "IST", days: 7, minutes: 120 },
`, 'config: formEndpoint')

  // ---- "Sign in": header and mobile menu
  html = edit(html,
    `    </nav>\n    <button class="nav__brochure" data-open="brochure">`,
    `    </nav>\n    <a class="nav__brochure" href="/" data-enter-app>Sign in</a>\n    <button class="nav__brochure" data-open="brochure">`,
    'header: Sign in')
  html = edit(html,
    `<a href="#faq">FAQ</a><a href="#contact">Contact</a>\n`,
    `<a href="#faq">FAQ</a><a href="#contact">Contact</a><a href="/" data-enter-app>Sign in</a>\n`,
    'mobile menu: Sign in')

  // ---- the demo time slot on both "Get demo trial" forms
  const improve = `<label class="fld fld--full"><span>What would you like to improve?</span><select name="improve" data-improve required><option value="">Select</option></select></label>`
  const slot = (extra) => `<label class="fld fld--full"${extra}><span>Demo time slot <em>(2 hours of WAREXX)</em></span><select name="slot" data-slots required><option value="">Choose a time</option></select></label>`
  if (html.split(improve).length - 1 !== 2) throw new Error('import-landing: expected the "improve" question on exactly two forms')
  // the contact section's form is always a demo form
  html = edit(html,
    `${improve}\n      </div>\n      <p class="form-err" role="alert" hidden></p>\n      <div class="lead-form__actions"><button class="btn btn--primary" type="submit">Get demo trial`,
    `${improve}\n        ${slot('')}\n      </div>\n      <p class="form-err" role="alert" hidden></p>\n      <div class="lead-form__actions"><button class="btn btn--primary" type="submit">Get demo trial`,
    'contact form: demo slot')
  // the pop-up form also serves Brochure, Pricing and the rest: demo mode only
  html = edit(html,
    `${improve}\n      <label class="fld fld--full" data-msg>`,
    `${improve}\n      ${slot(' data-demo-only hidden')}\n      <label class="fld fld--full" data-msg>`,
    'dialog form: demo slot')
  html = edit(html,
    `    $('[data-msg]', dialog).hidden = !c.msg;\n`,
    `    $('[data-msg]', dialog).hidden = !c.msg;\n    $$('[data-demo-only]', dialog).forEach(el => { el.hidden = type !== 'demo'; });\n`,
    'openLead: demo-only fields')

  // ---- sending: the lead API's answer is read (a slot comes back with a
  // sign-up pass), and the demo / sign-up / software are opened from here
  html = edit(html,
    `  async function sendLead(payload) {
    window.dispatchEvent(new CustomEvent('warexx:lead', { detail: payload }));
    if (!CFG.formEndpoint) { await wait(450); return; }
    const res = await fetch(CFG.formEndpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!res.ok) throw new Error('HTTP ' + res.status);
  }
`,
    `  // "Sign in" opens the WAREXX software, which is its own deployment (CFG.appUrl,
  // e.g. https://app.warexx.aavoraa.com/) with its own sign-in. This page holds
  // no session and grants none.
  function enterApp(delay = 0) {
    store.set('wrx_entered', '1');
    setTimeout(() => location.assign(CFG.appUrl || '/'), delay);
  }
  document.addEventListener('click', e => {
    if (!e.target.closest('[data-enter-app]')) return;
    e.preventDefault();
    enterApp();
  });

  // "Get demo trial" opens the live demo: the lead goes to formEndpoint (sales
  // are emailed), and the visitor is taken straight into the sample business.
  function openDemo() {
    let url = CFG.demoAppUrl || '/demo/';
    try { const u = new URL(url, location.href); u.searchParams.set('from', location.origin + location.pathname); url = u.href; } catch (e) {}
    location.assign(url);
  }
  // A booked slot: on to the demo's sign-up, the email already filled in. The
  // pass travels in the address's #fragment, which is never sent to a server.
  function openSignup(trial) {
    let url = CFG.demoAppUrl || '/demo/';
    try { const u = new URL(url, location.href); u.hash = 't=' + encodeURIComponent(trial.ticket); url = u.href; } catch (e) {}
    location.assign(url);
  }

  // Demo time slots: "Start now", then the daily slots for the next few days,
  // shown in the slot's own time zone (IST). The value is the slot's start time.
  function renderSlots() {
    const S = CFG.demoSlots || {}; const hours = S.hours || []; const off = (S.tzOffsetMinutes || 0) * 60000;
    const tz = S.tzLabel || ''; const len = (S.minutes || 120) * 60000; const now = Date.now();
    const fmtT = (ms) => { const d = new Date(ms + off); let h = d.getUTCHours(); const m = d.getUTCMinutes();
      const ap = h < 12 ? 'AM' : 'PM'; h = h % 12 || 12; return \`\${h}:\${String(m).padStart(2, '0')} \${ap}\`; };
    const day = (ms) => new Date(ms + off).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });
    let html = \`<option value="now">Start now · the next 2 hours</option>\`;
    const local = new Date(now + off); const y = local.getUTCFullYear(), mo = local.getUTCMonth(), d0 = local.getUTCDate();
    for (let d = 0; d < (S.days || 7); d++) {
      const opts = hours.map(h => Date.UTC(y, mo, d0 + d, h) - off).filter(t => t > now)
        .map(t => \`<option value="\${new Date(t).toISOString()}">\${fmtT(t)} – \${fmtT(t + len)} \${tz}</option>\`).join('');
      if (opts) html += \`<optgroup label="\${day(Date.UTC(y, mo, d0 + d) - off + 12 * 3600000)}">\${opts}</optgroup>\`;
    }
    $$('select[data-slots]').forEach(sel => { sel.innerHTML = '<option value="">Choose a time</option>' + html; });
  }

  async function sendLead(payload) {
    window.dispatchEvent(new CustomEvent('warexx:lead', { detail: payload }));
    if (!CFG.formEndpoint) { await wait(450); return {}; }
    const res = await fetch(CFG.formEndpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    try { return await res.json(); } catch (e) { return {}; }
  }
`, 'sendLead')

  html = edit(html,
    `      payload.submittedAt = new Date().toISOString();
      try {
        await sendLead(payload);
`,
    `      payload.submittedAt = new Date().toISOString();
      // the lead email carries "interest"; this design asks it as "improve"
      if (payload.improve && !payload.interest) payload.interest = payload.improve;
      try {
        if (payload.type === 'demo') {
          // A booked slot goes on to sign-up; otherwise (the trial not set up on
          // the server) the demo opens — a lead that fails to send must not keep
          // anyone out of the demo.
          let r = {};
          try { r = (await Promise.race([sendLead(payload), wait(8000)])) || {}; } catch (e) {}
          form.classList.add('is-done');
          const done = $('.lead-form__done', form); if (done) done.hidden = false;
          if (r.trial && r.trial.ticket) openSignup(r.trial); else openDemo();
          return;
        }
        await sendLead(payload);
`, 'submit: demo flow')

  html = edit(html, `  renderMisc();\n`, `  renderMisc();\n  renderSlots();\n`, 'boot: slots')
  return mergePricing(html)
}

// ---- pricing: a Demo card, where each plan's button goes, prices in the
// visitor's currency (landing/extras/local-pricing.js, inlined), and the
// enquiry the /pricing page sends for Monthly / Annual
const EXTRAS = path.join(FRONTEND, 'landing', 'extras')
const PRICING_CSS = `
/* WAREXX: four plans (Demo, Monthly, Annual, Custom), the pricing note, /pricing */
@media (min-width: 1240px) { .plans { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
@media (min-width: 900px) and (max-width: 1239px) { .plans { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: repeat(14, auto); } }
.plan:target { box-shadow: 0 0 0 2px #EE7A1E, var(--shadow); }
/* price row: "/ month" and "+ GST" stay whole and drop below a long price,
   rather than breaking into narrow stacked columns beside it */
.plan__price { flex-wrap: wrap; row-gap: 4px; }
.plan__per { display: inline-flex; align-items: baseline; gap: 6px; white-space: nowrap; }
.pricing__foot { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px 24px; margin-top: 18px; }
.pricing__note { margin: 0; font-size: 13.5px; color: var(--mute); }
.pmore { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.pmore__item { padding: clamp(20px, 2.2vw, 28px); border-radius: 20px; background: var(--surface); border: 1px solid var(--line); }
.pmore__item h3 { margin: 0 0 8px; font-size: 18px; }
.pmore__item p { margin: 0; color: var(--mute); }
.pmore__cta { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 24px; }
@media (max-width: 899px) { .pmore { grid-template-columns: minmax(0, 1fr); } }
`

export function mergePricing(html) {
  const script = readFileSync(path.join(EXTRAS, 'local-pricing.js'), 'utf8')
  html = edit(html, '</head>', () => `<style>${PRICING_CSS}</style>\n</head>`, 'pricing: styles')
  html = edit(html, '<script>/* js/main.js */',
    () => `<script>/* landing/extras/local-pricing.js */\n${script}</script>\n<script>/* js/main.js */`, 'pricing: script')
  html = edit(html, '<div class="plans" id="plans"></div>',
    '<div class="plans" id="plans"></div>\n    <div class="pricing__foot"><p class="pricing__note" data-pricing-note></p></div>',
    'pricing: note')
  html = edit(html, '    const pricing = CFG.pricing || {};\n',
    '    const pricing = CFG.pricing || {};\n    const CUR = window.WRX_CUR;   // prices in the visitor\'s currency (local-pricing.js)\n',
    'pricing: currency helper')
  html = edit(html, '<span class="plan__amount">${esc(p.price)}</span>',
    '<span class="plan__amount">${CUR ? CUR.money(p.price) : esc(p.price)}</span>', 'pricing: price')
  html = edit(html, '<span class="plan__tax">${esc(p.tax)}</span>',
    '<span class="plan__tax" data-tax="${esc(p.tax)}">${esc(p.tax)}</span>', 'pricing: tax')
  // "/ month + GST" as one piece: it moves under a long price together
  html = edit(html,
    '${isPh(p.period) ? \'\' : `<span class="plan__period">${esc(p.period)}</span>`}${p.tax ? `<span class="plan__tax" data-tax="${esc(p.tax)}">${esc(p.tax)}</span>` : \'\'}',
    '<span class="plan__per">${isPh(p.period) ? \'\' : `<span class="plan__period">${esc(p.period)}</span>`}${p.tax ? `<span class="plan__tax" data-tax="${esc(p.tax)}">${esc(p.tax)}</span>` : \'\'}</span>',
    'pricing: period and tax together')
  html = edit(html, '<article class="plan reveal${p.featured ? \' is-featured\' : \'\'}">',
    '<article class="plan reveal${p.featured ? \' is-featured\' : \'\'}"${p.slug ? ` id="plan-${esc(p.slug)}"` : \'\'}>', 'pricing: card id')
  const button = '<button class="btn ${p.featured ? \'btn--primary\' : \'btn--ghost\'} btn--block" data-open="${esc(p.open || \'plan\')}" data-interest="${esc(p.name)} plan">${esc(p.cta || \'View plan\')}</button>'
  html = edit(html, button,
    '${p.href ? `<a class="btn ${p.featured ? \'btn--primary\' : \'btn--ghost\'} btn--block" href="${esc(p.href)}">${esc(p.cta || \'View plan\')}</a>` : `'
    + button + '`}', 'pricing: card button')
  html = edit(html, '${p.addons.map(a => `<li>${esc(a)}</li>`).join(\'\')}',
    // one <span> per line: a card's list items are flex rows, and the converted
    // amount on its own would split "Additional POS: ₹4,999/month" into columns
    '${p.addons.map(a => `<li><span>${CUR ? CUR.money(a) : esc(a)}</span></li>`).join(\'\')}', 'pricing: add-ons')
  html = edit(html, "const note = $('[data-pricing-note]'); if (note && pricing.note) note.textContent = pricing.note;",
    "if (CUR) CUR.bind(); else { const note = $('[data-pricing-note]'); if (note && pricing.note) note.textContent = pricing.note; }",
    'pricing: note')
  html = edit(html, '    prebook: {',
    "    subscribe: { kicker: 'Choose a plan', title: 'Get started with WAREXX', sub: 'Tell us about your operation and we’ll set up your plan.', submit: 'Request this plan', done: 'Thank you. Our team will be in touch shortly to set up your plan.', msg: true },\n    prebook: {",
    'pricing: subscribe form copy')
  // the /pricing enquiry is a plan lead to the lead API (its email: "Your WAREXX plan enquiry")
  html = edit(html, "payload.type = form.dataset.type || 'demo';",
    "payload.type = form.dataset.type || 'demo';\n      if (payload.type === 'subscribe') payload.type = 'plan';", 'pricing: subscribe lead type')
  return html
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const src = process.argv[2]
  if (!src) { console.error('usage: node tools/import-landing.mjs <warexx-landing.html> [assets dir]'); process.exit(2) }
  const out = merge(readFileSync(src, 'utf8'))
  writeFileSync(DEST, out)
  console.log('[import-landing] wrote', path.relative(FRONTEND, DEST), `(${Math.round(out.length / 1024)} KB)`)
  const assets = process.argv[3]
  if (assets) {
    const video = path.join(assets, 'warexx-demo.mp4')
    if (existsSync(video)) {
      mkdirSync(path.join(FRONTEND, 'landing', 'assets'), { recursive: true })
      copyFileSync(video, path.join(FRONTEND, 'landing', 'assets', 'warexx-demo.mp4'))
      console.log('[import-landing] copied the film video to landing/assets/ (git-ignored)')
    }
  }
}
