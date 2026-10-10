// Pricing in the visitor's currency (landing/extras/local-pricing.js), as
// visitors from different countries would get it.
//
//   node tools/local_pricing_test.mjs
//
// Each case runs the script in a fresh sandbox with that visitor's time zone
// and browser languages, then checks the currency chosen, the converted prices
// (always rounded UP to a clean figure) and where each plan's button goes.
import { readFileSync } from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = readFileSync(path.join(HERE, '..', 'landing', 'extras', 'local-pricing.js'), 'utf8')
let bad = 0
const ok = (cond, what) => { console.log(`  ${cond ? 'ok  ' : 'FAIL'}  ${what}`); if (!cond) bad++ }

const plans = () => ({ pricing: { note: 'All prices are exclusive of GST.', plans: [
  { name: 'Monthly', price: '₹29,999', period: '/ month', tax: '+ GST', addons: ['Additional Warehouse: ₹10,000/month', 'Additional POS: ₹4,999/month'], cta: 'Get demo trial', open: 'demo' },
  { name: 'Annual', price: '₹3,29,000', period: '/ year', tax: '+ GST', cta: 'Get demo trial', open: 'demo' },
  { name: 'Custom', price: '', cta: 'Talk to our team', open: 'plan' },
] } })

function visit({ tz, langs, saved, pathname = '/', page }) {
  const store = new Map(saved ? [['wrx_currency', saved]] : [])
  const RealDTF = Intl.DateTimeFormat
  const FakeIntl = Object.create(Intl)
  FakeIntl.DateTimeFormat = function (...a) {
    const f = new RealDTF(...a)
    return { resolvedOptions: () => ({ ...f.resolvedOptions(), timeZone: tz }), format: (d) => f.format(d) }
  }
  const window = { WAREXX_CONFIG: plans() }
  const sandbox = {
    window, Intl: FakeIntl, console,
    navigator: { language: langs[0], languages: langs },
    localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) },
    location: { pathname },
    document: { documentElement: { dataset: page ? { page } : {} }, querySelectorAll: () => [] },
  }
  vm.createContext(sandbox)
  vm.runInContext(SRC, sandbox)
  return window
}

const raw = (inr, rate) => inr * rate
const RATES = JSON.parse(/\/\*RATES\*\/(\{[^}]*\})/.exec(SRC)[1])

console.log('which currency')
for (const [tz, langs, want] of [
  ['Asia/Kolkata', ['en-US'], 'INR'],            // an Indian browser set to US English
  ['Asia/Calcutta', ['en-IN'], 'INR'],
  ['America/New_York', ['en-US'], 'USD'],
  ['America/Toronto', ['en-CA'], 'CAD'],
  ['Europe/London', ['en-GB'], 'GBP'],
  ['Europe/Berlin', ['de-DE'], 'EUR'],
  ['Europe/Paris', ['fr-FR'], 'EUR'],
  ['Asia/Dubai', ['en-US'], 'AED'],             // time zone beats the language
  ['Asia/Riyadh', ['ar-SA'], 'SAR'],
  ['Asia/Singapore', ['en-SG'], 'SGD'],
  ['Australia/Perth', ['en-AU'], 'AUD'],        // not in the zone list: the language region decides
  ['Asia/Colombo', ['si-LK'], 'LKR'],
  ['Africa/Accra', ['en-GH'], 'USD'],           // a country without its own entry: dollars
  ['', [''], 'INR'],                            // nothing known: the prices as written
]) {
  const w = visit({ tz, langs })
  ok(w.WRX_CUR.code() === want, `${tz || '(no time zone)'} + ${langs[0] || '(no language)'} → ${w.WRX_CUR.code()} (want ${want})`)
}
ok(visit({ tz: 'America/New_York', langs: ['en-US'], saved: 'EUR' }).WRX_CUR.code() === 'EUR', 'a currency picked by the visitor wins, and is remembered')

console.log('converted and rounded up')
const us = visit({ tz: 'America/New_York', langs: ['en-US'] }).WRX_CUR
for (const [inr, want] of [[29999, 310], [329000, 3400], [10000, 105], [4999, 52]]) {
  const got = us.convert(inr)
  ok(got === want && got >= raw(inr, RATES.USD), `₹${inr.toLocaleString('en-IN')} → $${got} (exact $${raw(inr, RATES.USD).toFixed(2)}, want ${want})`)
}
for (const c of Object.keys(RATES)) {
  for (const inr of [4999, 10000, 29999, 329000]) {
    const got = us.convert(inr, c)
    if (got < raw(inr, RATES[c])) ok(false, `${c}: ₹${inr} rounded DOWN to ${got}`)
  }
}
ok(true, `every currency (${Object.keys(RATES).length}) rounds up, never down`)
ok(us.convert(29999, 'INR') === 29999 && us.format(329000, 'INR') === '₹3,29,000', 'rupee prices are shown exactly as written')
ok(/^\$310$/.test(us.format(29999)), `US English shows ${us.format(29999)}`)
ok(!/^\$/.test(us.format(29999, 'AUD')), `Australian dollars are not shown as a bare "$" (${us.format(29999, 'AUD')})`)
ok(us.money('Additional POS: ₹4,999/month').includes('data-inr="4999"'), 'add-on text: the rupee amount is converted in place')

console.log('plan buttons')
const land = visit({ tz: 'Asia/Kolkata', langs: ['en-IN'] }).WAREXX_CONFIG.pricing.plans
ok(land[0].name === 'Demo' && land[0].open === 'demo', 'a Demo card comes first and opens "Get demo trial"')
ok(land.find(p => p.name === 'Custom').open === 'demo', 'Custom opens "Get demo trial"')
ok(land.filter(p => /Monthly|Annual/.test(p.name)).every(p => /^\/pricing#plan-/.test(p.href)), 'Monthly and Annual open /pricing')
const page = visit({ tz: 'Asia/Kolkata', langs: ['en-IN'], pathname: '/pricing', page: 'pricing' }).WAREXX_CONFIG.pricing.plans
ok(page.filter(p => /Monthly|Annual/.test(p.name)).every(p => !p.href && p.open === 'subscribe'), 'on /pricing, Monthly and Annual open the plan enquiry')
ok(page.filter(p => p.name === 'Demo').length === 1, 'the Demo card is added once')

console.log(bad ? `\n${bad} FAILED` : '\nall passing')
process.exit(bad ? 1 : 0)
