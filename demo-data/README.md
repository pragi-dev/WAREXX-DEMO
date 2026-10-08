# Demo data: one fictional business

Everything the interactive demo shows comes from here: one consistent sample
business, "Sample Company", with 3 warehouses (WH-A, WH-B, WH-C), 6 stores,
suppliers, products, purchase orders, LRs, invoices, GRNs, stock movements,
transfers, returns, payments, audits, Store/POS sales and six months of
activity. No real customer data is in it, and none can get in: it is produced
from fictional seed scripts into a scratch database.

```
demo-data/
├── recorded/          WHAT THE DEMO SERVES (tracked in git)
│   ├── manifest.json  every recorded answer: method, warehouse, path, status, body
│   └── bodies/        larger answers (reports, POS pages, images)
├── tools/             HOW IT IS MADE (authoring only; needs ../../app/backend)
│   ├── build-data.mjs     all steps in one go
│   ├── seed_*.py          the fictional business
│   ├── record.mjs         headless browser tour → recorded/
│   ├── record_*.py        Ask WAREXX, item locator, audit answers
│   ├── rerender_*.py      Store/POS pages re-rendered from the seeded data
│   └── scrub.mjs          placeholder names + removes sign-in tokens
└── demo-build/        scratch databases while building (git-ignored)
```

## Why it is consistent

The answers were not written by hand. They were **recorded from the real WAREXX
backend** serving the seeded business, so the dashboard, inventory, reports,
search, product detail, stock movements and Ask WAREXX were all computed by the
same server from the same rows. The demo's service worker
(`../frontend/demo/public/sw.js`) only replays them, plus a few lookups that it
answers from the recording the way the server would (product search, item
locator, "open the second one").

## Rebuilding it

Re-record when the app's screens change (after `npm run sync:app`) or to move
the sample dates up to today. From the repository root, with the backend's
virtualenv set up (`app/setup.bat`) and Edge or Chrome installed:

```bash
node landing-demo/demo-data/tools/build-data.mjs
```

That one script:

1. **Seeds** scratch SQLite databases in `demo-data/demo-build/` with
   `seed_base.py`, `seed_purchasing.py`, `seed_operations.py`, `seed_shop.py`,
   then `seed_admin.py`. Each seeder **refuses to run unless its database is a
   SQLite file in a folder called `demo-build`**, because the first one drops
   every table. A production PostgreSQL database cannot be a target.
2. **Serves** it with the real backend (`app/backend`) on 127.0.0.1:8020, with
   no vision key (nothing is billed).
3. **Records** every screen with headless Edge/Chrome (`record.mjs`) into
   `recorded/`, passing every answer through `scrub.mjs`.

Then rebuild and check the demo:

```bash
cd landing-demo/frontend && npm run build:demo && npm test
```

The individual steps can also be run by hand. Each tool's header comment
gives its exact command, and `record_mobile.py`, `record_locator.py`,
`record_audit.py` and the `rerender_store_*.py` scripts add to an existing
recording in place.
