/* WAREXX — pricing in the visitor's currency, the Demo plan, and where each
   plan's button goes. Inlined into the landing page (and /pricing) by
   tools/import-landing.mjs, after WAREXX_CONFIG and before the page's main.js,
   so it survives every new version of the designer's page.

   PRICES are written once, in rupees, in WAREXX_CONFIG.pricing (₹29,999 …).
   Every "₹amount" on a plan card is converted to the visitor's currency at the
   rate below and ROUNDED UP to a clean figure (whole unit under 100, nearest 5
   under 1,000, nearest 10 under 10,000, nearest 100 above, nearest 1,000 over
   100,000) — never down, so no visitor is quoted less than the rupee price.
   Indian visitors see the rupee prices exactly as written.

   THE CURRENCY comes from the visitor's own browser — its time zone first
   (where the device is set), then its language region — so no request is
   made and nothing about the visitor is sent anywhere. A visitor can change it
   with the picker under the plans; the choice is remembered on that browser.

   THE RATES are rupees → currency. tools/landing.mjs refreshes them from
   open.er-api.com on every build (between the RATES markers below); the values
   here are the fallback when a build cannot reach it. */
window.WRX_CUR = (() => {
  'use strict';
  const RATES = /*RATES*/{"USD":0.010323,"EUR":0.009228,"GBP":0.007813,"AED":0.037912,"SAR":0.038712,"QAR":0.037577,"KWD":0.003206,"BHD":0.003882,"OMR":0.003969,"SGD":0.013238,"MYR":0.042248,"AUD":0.014792,"NZD":0.018399,"CAD":0.014736,"JPY":1.636,"CNY":0.06926,"HKD":0.08112,"KRW":13.848,"THB":0.34703,"IDR":184.69,"PHP":0.64882,"VND":266.62,"LKR":3.4196,"NPR":1.6,"BDT":1.2737,"PKR":2.8616,"ZAR":0.17135,"NGN":14.032,"KES":1.3411,"EGP":0.54137,"CHF":0.00859,"SEK":0.10327,"NOK":0.098909,"DKK":0.068992,"PLN":0.040441,"TRY":0.50942,"BRL":0.051858,"MXN":0.18899,"TWD":0.32981,"ILS":0.03162}/*/RATES*/;

  // time zone → currency, for the zones that say more than their continent
  const ZONES = {
    'Asia/Dubai': 'AED', 'Asia/Riyadh': 'SAR', 'Asia/Qatar': 'QAR', 'Asia/Kuwait': 'KWD', 'Asia/Bahrain': 'BHD',
    'Asia/Muscat': 'OMR', 'Asia/Singapore': 'SGD', 'Asia/Kuala_Lumpur': 'MYR', 'Asia/Kuching': 'MYR',
    'Asia/Tokyo': 'JPY', 'Asia/Shanghai': 'CNY', 'Asia/Chongqing': 'CNY', 'Asia/Hong_Kong': 'HKD', 'Asia/Seoul': 'KRW',
    'Asia/Bangkok': 'THB', 'Asia/Jakarta': 'IDR', 'Asia/Makassar': 'IDR', 'Asia/Jayapura': 'IDR', 'Asia/Manila': 'PHP',
    'Asia/Ho_Chi_Minh': 'VND', 'Asia/Saigon': 'VND', 'Asia/Colombo': 'LKR', 'Asia/Kathmandu': 'NPR', 'Asia/Katmandu': 'NPR',
    'Asia/Dhaka': 'BDT', 'Asia/Karachi': 'PKR', 'Asia/Taipei': 'TWD', 'Asia/Jerusalem': 'ILS', 'Asia/Tel_Aviv': 'ILS',
    'Europe/London': 'GBP', 'Europe/Belfast': 'GBP', 'Europe/Zurich': 'CHF', 'Europe/Stockholm': 'SEK', 'Europe/Oslo': 'NOK',
    'Europe/Copenhagen': 'DKK', 'Europe/Warsaw': 'PLN', 'Europe/Istanbul': 'TRY', 'Asia/Istanbul': 'TRY',
    'America/Toronto': 'CAD', 'America/Vancouver': 'CAD', 'America/Edmonton': 'CAD', 'America/Winnipeg': 'CAD',
    'America/Halifax': 'CAD', 'America/St_Johns': 'CAD', 'America/Regina': 'CAD', 'America/Montreal': 'CAD',
    'America/Mexico_City': 'MXN', 'America/Monterrey': 'MXN', 'America/Tijuana': 'MXN', 'America/Cancun': 'MXN',
    'America/Sao_Paulo': 'BRL', 'America/Bahia': 'BRL', 'America/Fortaleza': 'BRL', 'America/Manaus': 'BRL',
    'Africa/Johannesburg': 'ZAR', 'Africa/Lagos': 'NGN', 'Africa/Nairobi': 'KES', 'Africa/Cairo': 'EGP',
    'Australia/Sydney': 'AUD', 'Pacific/Auckland': 'NZD', 'Pacific/Chatham': 'NZD',
  };
  // country (language region, "en-GB" → GB) → currency
  const EURO = 'AT BE CY DE EE ES FI FR GR HR IE IT LT LU LV MT NL PT SI SK'.split(' ');
  const REGIONS = {
    US: 'USD', GB: 'GBP', AE: 'AED', SA: 'SAR', QA: 'QAR', KW: 'KWD', BH: 'BHD', OM: 'OMR', SG: 'SGD', MY: 'MYR',
    AU: 'AUD', NZ: 'NZD', CA: 'CAD', JP: 'JPY', CN: 'CNY', HK: 'HKD', KR: 'KRW', TH: 'THB', ID: 'IDR', PH: 'PHP',
    VN: 'VND', LK: 'LKR', NP: 'NPR', BD: 'BDT', PK: 'PKR', ZA: 'ZAR', NG: 'NGN', KE: 'KES', EG: 'EGP', CH: 'CHF',
    SE: 'SEK', NO: 'NOK', DK: 'DKK', PL: 'PLN', TR: 'TRY', BR: 'BRL', MX: 'MXN', TW: 'TWD', IL: 'ILS', IN: 'INR',
  };
  EURO.forEach(c => { REGIONS[c] = 'EUR'; });
  const known = c => c === 'INR' || !!RATES[c];

  function detect() {
    try { const saved = localStorage.getItem('wrx_currency'); if (saved && known(saved)) return saved; } catch (e) { /* storage off */ }
    let tz = '';
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { /* old browser */ }
    if (/^Asia\/(Kolkata|Calcutta)$/.test(tz)) return 'INR';
    if (ZONES[tz] && known(ZONES[tz])) return ZONES[tz];
    for (const l of (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || ''])) {
      const m = /[-_]([A-Za-z]{2})$/.exec(l || '');
      if (m && REGIONS[m[1].toUpperCase()] && known(REGIONS[m[1].toUpperCase()])) return REGIONS[m[1].toUpperCase()];
    }
    if (/^Europe\//.test(tz)) return 'EUR';
    if (/^Australia\//.test(tz)) return 'AUD';
    return tz ? 'USD' : 'INR';      // anywhere else abroad: dollars; nothing known: as written
  }

  let code = detect();
  const step = v => v < 100 ? 1 : v < 1000 ? 5 : v < 10000 ? 10 : v < 100000 ? 100 : v < 1e7 ? 1000 : 10000;
  /** a rupee amount in currency `c`, rounded UP to a clean figure */
  function convert(inr, c = code) {
    if (c === 'INR' || !RATES[c]) return inr;
    const v = inr * RATES[c]; const s = step(v);
    return Math.ceil(v / s - 1e-9) * s;
  }
  function format(inr, c = code) {
    if (c === 'INR' || !RATES[c]) return '₹' + Number(inr).toLocaleString('en-IN');
    const v = convert(inr, c);
    try {
      return new Intl.NumberFormat(navigator.language || 'en', { style: 'currency', currency: c, maximumFractionDigits: 0, minimumFractionDigits: 0 }).format(v);
    } catch (e) { return c + ' ' + v.toLocaleString(); }
  }
  const esc = s => String(s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  /** text with every "₹amount" in it shown in the visitor's currency (HTML) */
  function money(text) {
    return esc(text || '').replace(/₹\s?([\d,]+(?:\.\d+)?)/g, (_, n) => {
      const inr = +n.replace(/,/g, '');
      return `<span data-inr="${inr}">${esc(format(inr))}</span>`;
    });
  }
  function name(c) {
    try { return new Intl.DisplayNames([navigator.language || 'en'], { type: 'currency' }).of(c); } catch (e) { return c; }
  }
  function list() { return ['INR', ...Object.keys(RATES).sort()]; }

  /** show every price on the page in currency `c` */
  function apply(c, remember) {
    if (!known(c)) return;
    code = c;
    if (remember) { try { localStorage.setItem('wrx_currency', c); } catch (e) { /* storage off */ } }
    document.querySelectorAll('[data-inr]').forEach(el => { el.textContent = format(+el.dataset.inr); });
    // GST is an Indian tax: "+ GST" for rupee prices, "+ taxes" elsewhere
    document.querySelectorAll('[data-tax]').forEach(el => { el.textContent = c === 'INR' ? el.dataset.tax : '+ taxes'; });
    const P = (window.WAREXX_CONFIG || {}).pricing || {};
    document.querySelectorAll('[data-pricing-note]').forEach(el => {
      el.textContent = c === 'INR' ? (P.note || '')
        : `Prices in ${name(c)} (${c}), converted from Indian rupees and rounded up. Taxes as applicable.`;
    });
    document.querySelectorAll('select[data-currency]').forEach(s => { s.value = c; });
  }
  /** fill the currency pickers and keep them in step */
  function bind() {
    document.querySelectorAll('select[data-currency]').forEach(s => {
      if (!s.options.length) s.innerHTML = list().map(c => `<option value="${c}">${c} · ${esc(name(c))}</option>`).join('');
      s.value = code;
      s.onchange = () => apply(s.value, true);
    });
    apply(code);
  }

  /* ---- the plans: a Demo card first, and where each button goes -------------
     Demo and Custom open the "Get demo trial" form. Monthly and Annual say
     "Subscribe" and open the payment page (paymentUrl); with none set yet, a
     plan enquiry (sent to the lead API as a "plan" lead). The same on the
     landing page and on /pricing. Keyed by plan name, so the designer's own
     plans, prices and wording are kept as they come. */
  const PLANS_PAGE = document.documentElement.dataset.page === 'pricing'
    || /^\/pricing\/?$/.test(location.pathname);
  const P = (window.WAREXX_CONFIG || {}).pricing;
  if (P && Array.isArray(P.plans)) {
    if (!P.plans.some(p => /^\s*demo\s*$/i.test(p.name || ''))) {
      P.plans.unshift({
        // a price card: the designer's cards show a price OR a summary in the
        // same row, so the Demo card's line goes in the note row instead
        name: 'Demo', price: 'Free', period: '· 2 hours', note: 'No payment, no commitment.',
        includes: ['The full WAREXX software', 'A sample business, ready to explore', 'Book a slot that suits you'],
        cta: 'Get demo trial', open: 'demo', featured: false,
      });
    }
    for (const p of P.plans) {
      const n = (p.name || '').toLowerCase();
      p.slug = n.replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      if (/^(monthly|annual)/.test(n)) {
        // "Subscribe" goes to the payment page (WAREXX_CONFIG.paymentUrl, set at
        // build time from VITE_PAYMENT_URL) with the plan named: ?plan=monthly.
        // Until there is one, it opens the plan enquiry instead of a dead link.
        p.cta = 'Subscribe';
        const pay = String((window.WAREXX_CONFIG || {}).paymentUrl || '').trim();
        if (pay) { p.href = pay + (pay.includes('?') ? '&' : '?') + 'plan=' + encodeURIComponent(p.slug); }
        else { p.href = ''; p.open = 'subscribe'; }
      } else if (/^demo/.test(n)) {
        p.open = 'demo'; p.href = ''; p.cta = 'Get demo trial';
      } else if (/^custom/.test(n)) {
        // the designer's own wording ("Talk to our team"); the demo form behind it
        p.open = 'demo'; p.href = ''; p.cta = p.cta || 'Talk to our team';
      }
    }
  }

  return { code: () => code, convert, format, money, apply, bind, list, name, plansPage: PLANS_PAGE };
})();
