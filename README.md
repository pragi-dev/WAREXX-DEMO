# WAREXX: public website + interactive demo

Two static sites built from one project. Neither one needs the production
software (`../app`) to be running or deployed, and neither one can reach it.

| | Landing page | Interactive demo |
|---|---|---|
| Address | `https://warexx.aavoraa.com` | `https://demo.warexx.aavoraa.com` |
| Source | `frontend/landing/index.html` | `frontend/demo/`, running `frontend/vendor/warexx-app` |
| Data | none | `demo-data/recorded/` (one fictional business) |
| Server | `api/leads.py` (lead form emails), nothing else | none |
| Auth | none | none: every visitor is the demo's "Sample Admin" |

```
landing-demo/
├── frontend/
│   ├── landing/index.html     the marketing page (hero, product story, features,
│   │                          industries, film, live demo, pricing, testimonials,
│   │                          FAQ, brochure, contact / book a demo)
│   ├── demo/                  index.html + main.jsx (start-up), demo.css,
│   │   └── public/            sw.js (the demo's stand-in server), m/demo-boot.js,
│   │                          _headers / _redirects (Netlify, Cloudflare Pages)
│   ├── vendor/warexx-app/     snapshot of app/frontend/src + app/backend/app/mobile
│   ├── tools/
│   │   ├── landing.mjs        landing build + dev server
│   │   ├── mobile-page.mjs    the phone app (/m) for the demo
│   │   ├── sync-app.mjs       refresh vendor/warexx-app from ../app
│   │   ├── isolation_test.mjs npm test
│   │   └── demo_browser_test.mjs  headless-browser test of a running demo
│   ├── vite.demo.config.js
│   ├── vercel.json            ← the DEMO site's Vercel config (project root: landing-demo/frontend)
│   └── package.json
├── demo-data/
│   ├── recorded/              manifest.json + bodies/ (what the demo shows)
│   └── tools/                 seeders + recorder (need ../app/backend; authoring only)
├── lead-api/                  lead_mail.py, http_handler.py, server.py (stdlib Python)
├── api/leads.py               the lead API as a Vercel function
├── vercel.json                ← the LANDING site's Vercel config (project root: landing-demo)
├── run_landing.bat / run_demo_static.bat
├── .env.example
└── package.json               shortcuts (npm run dev, build, test…)
```

## Develop

```bash
cd landing-demo/frontend && npm ci
```

| Command (in `frontend/`) | What |
|---|---|
| `npm run dev:landing` | landing page on http://localhost:5174; forms go to the lead API on :8003 |
| `python ../lead-api/server.py` | the lead API on http://127.0.0.1:8003 (emails saved to `lead-api/outbox/*.eml` unless `LEAD_MAIL_BACKEND=smtp`) |
| `npm run dev:demo` | demo on http://localhost:8002 |
| `npm run build` | `dist/landing/` and `dist/demo/` |
| `npm run build:landing` / `npm run build:demo` | one of them |
| `npm run preview:demo` | serve `dist/demo` on :8002 |
| `npm test` | isolation + data + secrets checks on the builds |
| `node tools/demo_browser_test.mjs http://localhost:8002/` | the demo in headless Edge/Chrome (`PROD_URL=…` also proves production is unreachable) |

On Windows, `run_landing.bat` starts the landing page and its lead API, and
`run_demo_static.bat` builds and serves the demo.

The landing page is shipped exactly as written. `tools/landing.mjs` only fills
in three addresses and adds a Content-Security-Policy; it is not run through
Vite. The demo build is Vite.

## Environment variables

See [.env.example](.env.example). Two kinds, kept apart:

- **`VITE_*` are public**: written into the pages at build time, readable by anyone.
  Addresses only: `VITE_LEAD_ENDPOINT`, `VITE_DEMO_URL`, `VITE_APP_URL`,
  `VITE_LANDING_URL`. Unset, they default to localhost in development and the
  `*.warexx.aavoraa.com` addresses in a build. **Never put a secret in a `VITE_*` variable.**
- **`LEAD_*` are server-side**: the lead API's mail settings (SMTP host, user,
  password, from/to). Set them on the host running `api/leads.py` /
  `lead-api/server.py`. They are never part of a build.

There is no database setting. Nothing here knows the production database URL,
auth secret, or any API key, and nothing should.

## The demo

The demo is the WAREXX software's own UI (a snapshot in `vendor/warexx-app`)
with a service worker, `demo/public/sw.js`, standing in for the server. Every
request the UI makes to `/api` or `/pos` is answered by the worker from
`demo-data/recorded`:

- **Reads** come from the recording, made off the real backend running the
  sample business, so every screen agrees with every other.
- **Writes** (create PO, GRN, stock inward/outward, transfers…) are acknowledged
  so the screen behaves as if it saved, and discarded. Nothing leaves the browser.
  **Start over** in the banner, or a reload, resets the demo.
- **Ask WAREXX** answers from the recorded answers the real assistant gave over
  the same data, including differently worded questions ("Which products are
  low in stock?" matches the recorded "show low stock items"). Unrelated questions
  are told they are not part of the demo, with suggestions.
- **Sign-in** is answered by the worker: whatever is typed, the visitor is the
  demo's Sample Admin with the token `"demo"`.

Every demo page carries `Content-Security-Policy: connect-src 'self'`, so the
browser itself refuses a request to any other host, including the production API.

### Refreshing the app snapshot

`vendor/warexx-app` is a copy, so the demo cannot be broken by a change in
`app/`, and the snapshot always matches the recording. To bring in a newer UI:

```bash
npm run sync:app      # copies app/frontend/src and app/backend/app/mobile
```

Then re-record the data (below) if the new UI calls endpoints the recording
lacks, and `npm run build && npm test`.

### Rebuilding the demo data

See [demo-data/README.md](demo-data/README.md). In short:
`node demo-data/tools/build-data.mjs` seeds a scratch database with the
fictional business, serves it with the real backend from `../app/backend` on a
spare port (no vision key, nothing billed), records every screen into
`demo-data/recorded`, and scrubs it. It never touches a real database: each
seeder refuses to run unless its database is a SQLite file in a folder named
`demo-build`.

## Deploy

**Landing page** (Vercel): new project, **Root Directory `landing-demo`**.
`vercel.json` there builds `frontend/dist/landing` and deploys `api/leads.py`
as the `/api/leads` function. Set the `LEAD_*` variables in the project's
environment settings. Domain: `warexx.aavoraa.com`.

**Demo** (Vercel): a second project, **Root Directory `landing-demo/frontend`**
(keep "Include files outside the root directory" on, because the build reads
`../demo-data`). `frontend/vercel.json` builds `dist/demo` and sets the CSP and
service-worker headers. Domain: `demo.warexx.aavoraa.com`.

**Any other static host** (Netlify, Cloudflare Pages, S3 + CloudFront, nginx):
`npm ci && npm run build`, then publish `frontend/dist/landing` and
`frontend/dist/demo` as two sites. Both include `_headers` / `_redirects` for
Netlify and Cloudflare Pages. Rules for the demo:

1. Serve it at the **root of its own origin**: its service worker must answer `/api/…` and `/pos/…`.
2. Use **HTTPS** (or localhost): browsers only run service workers there.
3. Send the CSP header `connect-src 'self'; form-action 'self'; base-uri 'self'` (the pages also carry it as a meta tag).

Without Vercel, the lead form needs `lead-api/server.py` running somewhere
(stdlib only). Point `VITE_LEAD_ENDPOINT` at it and list the landing origin in
`LEAD_ALLOWED_ORIGINS`. Or leave `VITE_LEAD_ENDPOINT` empty to run with no
backend (forms show success and emit a `warexx:lead` browser event).

The Android and iOS **Demo** apps (in `../app`) bundle `frontend/dist/demo`: build
it first (`npm run build:demo`). The production (Live) apps do not use it.
