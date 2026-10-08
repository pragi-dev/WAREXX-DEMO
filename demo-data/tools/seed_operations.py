"""Stock operations and money for the static demo — run AFTER seed_base.py.

    set ESSA_DATABASE_URL=sqlite:///C:/path/to/demo-build/demo.db
    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\seed_base.py
    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\seed_operations.py

seed_base.py writes the business and its receipts. This writes what happens to
the goods and the bills afterwards: dispatches to the stores and between the
warehouses, debit notes back to suppliers, payments against the GRNs, price
revisions, shelf checks and counted stocktakes, put-away racks for the cartons,
label templates and print history, and clearance worksheets over the old lots.

Everything that moves stock or money goes through the application's own
services, the same functions the screens call, and is then backdated across the
last ~60 days so the charts have shape. Rows are looked up, never assumed: a
purchasing seeder may have run in between and added its own.

Like seed_base.py it refuses any database that is not a scratch file inside a
'demo-build' folder. It does not drop anything, but it is still only for there.
"""
import datetime as dt
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "..", "app", "backend"))
sys.path.insert(0, BACKEND)

URL = os.environ.get("ESSA_DATABASE_URL", "")
if not URL.startswith("sqlite:///") or "demo-build" not in URL.replace("\\", "/"):
    sys.exit("Refusing: ESSA_DATABASE_URL must be a sqlite file inside a 'demo-build' "
             "folder. This script writes demo data into it.")

random.seed(11)         # the same story on every build

from app import models                                             # noqa: E402
from app.database import SessionLocal                              # noqa: E402
from app.services import bundles as bundle_svc                     # noqa: E402
from app.services import dead_stock as ds                          # noqa: E402
from app.services import label_designer as ld                      # noqa: E402
from app.services import outward as out_svc                        # noqa: E402
from app.services import payments as pay_svc                       # noqa: E402
from app.services import physical_audit as pa_svc                  # noqa: E402
from app.services import pricing as price_svc                      # noqa: E402
from app.services import returns as ret_svc                        # noqa: E402
from app.services import stock_audit as sa_svc                     # noqa: E402
from app.services import stock_locations as stock_loc              # noqa: E402
from app.services import units as unit_svc                         # noqa: E402

NOW = dt.datetime.utcnow()
TODAY = NOW.replace(hour=6, minute=0, second=0, microsecond=0)

PACKERS = ["Sample Staff 01", "Sample Staff 02", "Sample Staff 03", "Sample Staff 08", "Sample Staff 09"]
STORE_RECEIVERS = {
    "ST-01": ["Sample Staff 11", "Sample Staff 12"], "ST-02": ["Sample Staff 13", "Sample Staff 14"],
    "ST-03": ["Sample Staff 15", "Sample Staff 16"], "ST-04": ["Sample Staff 17", "Sample Staff 18"],
    "ST-05": ["Sample Staff 19", "Sample Staff 20"], "ST-06": ["Sample Staff 21", "Sample Staff 22"],
}
WH_RECEIVERS = ["Sample Staff 04", "Sample Staff 05", "Sample Staff 10"]
COUNTERS = ["Sample Staff 06", "Sample Staff 07", "Sample Staff 23", "Sample Staff 24"]
MANAGERS = ["superadmin", "Sample Staff 25", "Sample Staff 26"]


def ago(days, hours=0, minutes=0):
    """A moment `days` back from this morning, `hours` into the working day."""
    return TODAY - dt.timedelta(days=days) + dt.timedelta(hours=hours, minutes=minutes)


def _restamp(db, ref_types, ref_id, when):
    db.query(models.StockMovement).filter(
        models.StockMovement.ref_id == ref_id,
        models.StockMovement.ref_type.in_(ref_types)).update(
        {"created_at": when}, synchronize_session=False)


# ---------------------------------------------------------------------------
def run(db):
    if db.query(models.Payment).count() or db.query(models.ClearanceCampaign).count():
        print("seed_operations: payments / clearance already present — nothing to do")
        return
    whs = {w.code: w for w in db.query(models.Warehouse).all()}
    stores = {s.code: s for s in db.query(models.Store).all()}
    if not whs or not stores:
        sys.exit("seed_operations: run seed_base.py first (no warehouses / stores)")

    _grn_totals(db)
    # What is idle BEFORE anything here moves: the dead-stock screen is built on
    # these lots, so nothing below dispatches them and resets their clock.
    idle = {r["product_id"] for r in ds.product_rows(db) if r["status"] != "healthy"}
    counts = {}
    counts["bundles_located"] = _bundles(db)
    counts["outwards"] = _outwards(db, whs, stores, idle)
    counts["returns"] = _returns(db)
    counts["payments"] = _payments(db)
    counts["price_revisions"] = _pricing(db, idle)
    counts["shelf_checks"] = _stock_audits(db, whs)
    counts["physical_audits"] = _physical_audits(db, whs)
    counts["label_templates"] = _labels(db)
    counts["clearance_campaigns"] = _clearance(db)
    db.commit()
    print("seed_operations:", counts)


# ---------------------------------------------------------------------------
#  GRN totals — the base leaves them at 0, and a payable of 0 is never pending
# ---------------------------------------------------------------------------
def _grn_totals(db):
    """Taxable = the invoice lines; GST at the garment slabs (5% on a piece up to
    ₹1,000, 12% above) — what the supplier's bill would have carried."""
    for p in db.query(models.Purchase).all():
        if p.grand_total:
            continue
        taxable = tax = 0.0
        for l in p.lines:
            amt = float(l.amount or (l.qty or 0) * (l.rate or 0))
            taxable += amt
            tax += amt * (0.05 if float(l.rate or 0) <= 1000 else 0.12)
        if not taxable:
            continue
        p.taxable_total = round(taxable, 2)
        p.tax_total = round(tax, 2)
        p.grand_total = float(round(taxable + tax))
    db.commit()


# ---------------------------------------------------------------------------
#  8. Put-away racks — the item locator's "where is it"
# ---------------------------------------------------------------------------
def _bundles(db):
    zones = {"WH-A": ["A", "B", "C", "D"], "WH-B": ["K", "M", "N"], "WH-C": ["S", "T"]}
    rows = (db.query(models.Bundle).join(models.Purchase,
                                         models.Bundle.purchase_id == models.Purchase.id)
            .order_by(models.Bundle.id).all())
    newest = {p.id for p in db.query(models.Purchase).filter(
        models.Purchase.status == "posted").order_by(
        models.Purchase.posted_at.desc()).limit(2)}
    located = 0
    for i, b in enumerate(rows):
        wh = b.purchase.warehouse if b.purchase else None
        if not wh:
            continue
        received = b.purchase.posted_at or b.received_at or ago(30)
        if b.purchase_id in newest and i % 2:     # the newest wait to be put away
            continue
        zone = random.choice(zones.get(wh.code, ["A"]))
        loc = f"{wh.code}-{zone}{random.randint(1, 12):02d}-{random.choice('1234')}"
        bundle_svc.locate(db, b, loc, by=random.choice(PACKERS))
        b.located_at = received + dt.timedelta(hours=random.randint(3, 30))
        b.received_at = received
        located += 1
        age = (TODAY - received).days
        if age > 25 and i % 3 != 2:
            bundle_svc.open_bundle(db, b)
            b.opened_at = b.located_at + dt.timedelta(days=random.randint(1, 4))
            if i % 3 == 0:
                try:
                    bundle_svc.tag(db, b, by=random.choice(COUNTERS))
                    b.tagged_at = b.opened_at + dt.timedelta(hours=random.randint(2, 20))
                except ValueError:
                    pass                    # undetailed items: stays opened
    db.commit()
    return located


# ---------------------------------------------------------------------------
#  3. Stock Outward / Inward
# ---------------------------------------------------------------------------
def _first_in(db):
    out = {}
    for pid, wid, when in db.query(models.StockMovement.product_id,
                                   models.StockMovement.warehouse_id,
                                   models.StockMovement.created_at).filter(
            models.StockMovement.qty_delta > 0):
        k = (pid, wid)
        if when and (k not in out or when < out[k]):
            out[k] = when
    return out


def _outwards(db, whs, stores, idle):
    # (days ago, from, ("store"|"wh", code), final status, short on receipt)
    plan = [
        (54, "WH-A", ("store", "ST-01"), "received", False),
        (51, "WH-C", ("store", "ST-05"), "received", False),
        (47, "WH-B", ("store", "ST-03"), "received", False),
        (44, "WH-A", ("store", "ST-02"), "received", False),
        (41, "WH-C", ("store", "ST-06"), "received", False),
        (38, "WH-A", ("store", "ST-01"), "received", False),
        (35, "WH-B", ("store", "ST-04"), "received", False),
        (31, "WH-B", ("store", "ST-03"), "received", True),
        (27, "WH-A", ("wh", "WH-B"), "received", False),
        (24, "WH-A", ("store", "ST-01"), "received", False),
        (21, "WH-C", ("store", "ST-05"), "received", False),
        (19, "WH-B", ("wh", "WH-A"), "received", True),
        (17, "WH-A", ("store", "ST-02"), "received", False),
        (15, "WH-C", ("store", "ST-06"), "received", True),
        (13, "WH-B", ("store", "ST-03"), "received", False),
        (11, "WH-A", ("store", "ST-01"), "received", True),
        (8, "WH-B", ("store", "ST-04"), "received", False),
        (6, "WH-C", ("store", "ST-05"), "posted", False),
        (4, "WH-A", ("store", "ST-02"), "posted", False),
        (3, "WH-A", ("wh", "WH-B"), "posted", False),
        (3, "WH-B", ("store", "ST-03"), "posted", False),
        (2, "WH-C", ("store", "ST-06"), "posted", False),
        (1, "WH-A", ("store", "ST-01"), "posted", False),
        (0, "WH-B", ("store", "ST-04"), "draft", False),
        (0, "WH-A", ("store", "ST-02"), "draft", False),
    ]
    first_in = _first_in(db)
    products = db.query(models.Product).order_by(models.Product.id).all()
    made = 0
    for days, frm, (kind, to_code), status, short in plan:
        src = whs.get(frm)
        if not src:
            continue
        when = ago(days, hours=random.randint(3, 6), minutes=random.randint(0, 59))
        cands = []
        for p in products:
            if p.id in idle:
                continue
            got = first_in.get((p.id, src.id))
            have = stock_loc.qty_at(db, p.id, src.id)
            if got and got < when - dt.timedelta(hours=20) and have >= 4:
                cands.append((p, have))
        if not cands:
            continue
        random.shuffle(cands)
        lines = []
        for p, have in cands[:random.randint(2, 5)]:
            pricey = float(p.last_rate or p.avg_cost or 0) >= 2000
            q = random.choice([1, 2, 2, 3]) if pricey else random.choice([4, 6, 6, 8, 10, 12])
            q = max(1, min(q, int(have // 3) or 1))
            lines.append({"product_id": p.id, "qty": q})
        payload = {"date": when.strftime("%Y-%m-%d"), "from_warehouse_id": src.id,
                   "packed_by": random.choice(PACKERS), "lines": lines}
        if kind == "store":
            payload["to_store_id"] = stores[to_code].id
        else:
            payload["to_warehouse_id"] = whs[to_code].id
        o = out_svc.create_outward(db, payload)
        o.created_at = when - dt.timedelta(minutes=40)
        db.commit()
        made += 1
        if status == "draft":
            continue
        res = out_svc.post_outward(db, o)
        if not res.get("ok"):
            db.rollback()
            print("outward not posted", o.code, res)
            continue
        o.posted_at = when
        db.commit()
        _restamp(db, ("outward",), o.id, when)
        if status == "received":
            recv = when + dt.timedelta(days=1 if days % 2 else 2, hours=random.randint(-2, 3))
            accepted = None
            if short and o.lines:
                l = o.lines[0]
                accepted = {l.id: max(0, float(l.qty) - 1)}
            who = (random.choice(STORE_RECEIVERS.get(to_code, ["Sample Staff 27"]))
                   if kind == "store" else random.choice(WH_RECEIVERS))
            res = out_svc.receive_outward(db, o, accepted=accepted, received_by=who,
                                          date=recv.strftime("%Y-%m-%d"))
            if res.get("ok"):
                o.received_at = recv
                db.commit()
                _restamp(db, ("transfer_in",), o.id, recv)
        db.commit()
    return made


# ---------------------------------------------------------------------------
#  2. Purchase returns (debit notes)
# ---------------------------------------------------------------------------
RETURN_PLAN = [
    # (warehouse code, nth posted GRN there oldest-first (negative = from newest),
    #  days after receipt, reason, post?)
    ("WH-B", 0, 24, "Slow-moving lot — supplier agreed to take back unsold sizes", True),
    ("WH-B", 3, 9, "Colour bleeding on wash test", True),
    ("WH-A", 2, 6, "Wrong size ratio supplied against order", True),
    ("WH-C", 0, 5, "Weave defect on border — supplier to replace", True),
    ("WH-A", 5, 4, "Stitching defects found at tagging", True),
    ("WH-A", -1, 3, "Damaged in transit — torn outer packing", False),
]


def _returns(db):
    posted = (db.query(models.Purchase).filter(models.Purchase.status == "posted")
              .order_by(models.Purchase.posted_at, models.Purchase.id).all())
    made = 0
    used = set()
    for wh_code, idx, after, reason, post in RETURN_PLAN:
        here = [p for p in posted if p.warehouse and p.warehouse.code == wh_code]
        if not here:
            continue
        p = here[max(-len(here), min(idx, len(here) - 1))]
        if p.id in used:
            continue
        used.add(p.id)
        base = p.posted_at or p.created_at or ago(30)
        when = min(base + dt.timedelta(days=after, hours=3), ago(1, hours=5))
        ret = ret_svc.build_from_purchase(db, p)
        db.flush()
        db.refresh(ret)              # its lines were added behind the relationship
        qtys = {}
        for l in ret.lines:
            if l.is_shortage_claim or not l.product_id:
                continue
            have = stock_loc.qty_at(db, l.product_id, p.warehouse_id)
            if have >= 4:
                qtys[l.id] = float(min(random.choice([2, 3, 4, 6]), int(have // 3) or 1))
            if len(qtys) >= random.randint(1, 3):
                break
        if not qtys:
            db.rollback()
            continue
        ret_svc.set_lines(db, ret, qtys)
        ret.created_at = when - dt.timedelta(hours=1)
        if post:
            res = ret_svc.post(db, ret, reason=reason, date=when.strftime("%Y-%m-%d"))
            if not res.get("ok"):
                db.rollback()
                print("return not posted", res)
                continue
            ret.posted_at = when
            db.commit()
            _restamp(db, ("purchase_return",), ret.id, when)
        else:
            ret.reason = reason
        db.commit()
        made += 1
    return made


# ---------------------------------------------------------------------------
#  1. Supplier payments
# ---------------------------------------------------------------------------
OUR_BANK = "Sample Bank · CA 0000 0000 0001 · ABC Area"


def _payments(db):
    from app.services import dates
    bills = {b["purchase_id"]: b for b in pay_svc._pending_bills(db, None)}
    posted = (db.query(models.Purchase).filter(models.Purchase.status == "posted",
                                               models.Purchase.id.in_(list(bills)))
              .order_by(models.Purchase.invoice_date, models.Purchase.id).all())
    # (date, supplier_id, [(purchase, cash, discount, tds)], mode)
    pending = []
    by_sup_seen = {}
    for i, p in enumerate(posted):
        inv_date = dates.parse(p.invoice_date)
        if not inv_date:
            continue
        age = (TODAY.date() - inv_date).days
        due = bills[p.id]["outstanding"]
        if age > 150 and i == 1:
            continue                      # one old bill left overdue on purpose
        if age < 28:
            continue                      # not yet due
        if age <= 50:
            share = [0.0, 0.5, 1.0, 0.6][i % 4]
        else:
            share = 0.65 if i % 7 == 3 else 1.0   # one older bill only part-settled
        if not share:
            continue
        disc = round(due * 0.02) if (share == 1.0 and i % 4 == 0) else 0.0
        tds = round(due * 0.001) if (share == 1.0 and due > 50000) else 0.0
        cash = round(due * share - disc - tds) if share < 1 else round(due - disc - tds, 2)
        pay_day = min(inv_date + dt.timedelta(days=random.randint(18, 40)),
                      TODAY.date() - dt.timedelta(days=1))
        key = (p.supplier_id, pay_day.isoformat()[:7])
        if share == 1.0 and key in by_sup_seen:
            by_sup_seen[key][2].append((p, cash, disc, tds))   # one cheque, two bills
            continue
        entry = [pay_day, p.supplier_id, [(p, cash, disc, tds)]]
        pending.append(entry)
        if share == 1.0:
            by_sup_seen[key] = entry
    # an advance part-payment on a recent bill, so "partly paid" shows young too
    young = [p for p in posted if dates.parse(p.invoice_date)
             and 7 <= (TODAY.date() - dates.parse(p.invoice_date)).days < 28]
    if young:
        p = young[0]
        pending.append([TODAY.date() - dt.timedelta(days=2), p.supplier_id,
                        [(p, round(bills[p.id]["outstanding"] * 0.3), 0.0, 0.0)]])

    pending.sort(key=lambda e: e[0])
    made = 0
    for n, (day, sup_id, allocs) in enumerate(pending):
        total = sum(a[1] for a in allocs)
        if total > 200000:
            mode = "RTGS"
        elif total < 15000:
            mode = "UPI"
        else:
            mode = ["NEFT", "Cheque", "NEFT", "Cheque", "NEFT"][n % 5]
        sup = db.get(models.Supplier, sup_id)
        payload = {"supplier_id": sup_id, "date": day.isoformat(), "mode": mode,
                   "bank": OUR_BANK,
                   "remarks": ("Full settlement" if all(a[0] and abs(a[1] + a[2] + a[3]
                               - bills[a[0].id]["outstanding"]) < 1 for a in allocs)
                               else "Part payment as agreed with supplier"),
                   "allocations": [{"purchase_id": a[0].id, "cash": a[1], "discount": a[2],
                                    "tds": a[3]} for a in allocs]}
        if mode == "Cheque":
            payload["cheque_no"] = f"{random.randint(100000, 999999)}"
            payload["cheque_date"] = day.isoformat()
            payload["ref_no"] = f"CHQ {payload['cheque_no']}"
        elif mode == "UPI":
            payload["ref_no"] = f"UPI/{day:%y%m%d}/{random.randint(10**11, 10**12 - 1)}"
        else:
            payload["ref_no"] = f"SMPL{'R' if mode == 'RTGS' else 'N'}{day:%Y%m%d}{random.randint(10**7, 10**8 - 1)}"
        if any(a[2] for a in allocs):
            payload["remarks"] += " · 2% early-payment discount"
        if any(a[3] for a in allocs):
            payload["remarks"] += " · TDS 194Q deducted"
        pay = pay_svc.create_payment(db, payload)
        pay.created_at = dt.datetime.combine(day, dt.time(10 + n % 6, (n * 7) % 60))
        db.commit()
        made += 1
    return made


# ---------------------------------------------------------------------------
#  4. Price changer
# ---------------------------------------------------------------------------
def _pricing(db, idle):
    idle_ids = sorted(idle)[:10]
    plan = [
        (56, "sale_price", "discount_off_mrp", 10, 10, {"brand": "Sample Brand A"}, "", None,
         "Sample offer 1 — 10% off MRP on Sample Brand A, effective from today", "Sample Staff 26"),
        (44, "mrp", "percent", 5, 50, {"brand": "Sample Brand D"}, "", None,
         "Supplier rate revision — new MRP from 1st of the month", "superadmin"),
        (36, "sale_price", "amount", -40, None, {"brand": "Sample Brand J"}, "", None,
         "Sample offer 2 — flat ₹40 off on kids wear", "Sample Staff 25"),
        (29, "sale_price", "discount_off_mrp", 30, 10, None, "", idle_ids,
         "Clearance markdown on 90+ day stock (dead-stock ladder)", "superadmin"),
        (21, "sale_discount_pct", "set", 15, None, {"brand": "Sample Brand K"}, "", None,
         "Sample offer 3 — 15% discount for one month", "Sample Staff 26"),
        (14, "sale_price", "percent", 8, 10, None, "SAMPLE PIECE 010", None,
         "Markup: revised costing after material rate increase", "Sample Staff 25"),
        (9, "sale_price", "set", 429, None, None, "SAMPLE PIECE 003", None,
         "Price point aligned with Sample Store 3", "Sample Staff 26"),
        (5, "sale_price", "amount", -50, None, {"brand": "Sample Brand G"}, "", None,
         "Sample offer 4 — flat ₹50 off on Sample Brand G", "superadmin"),
        (2, "sale_price", "percent", -5, 10, None, "SAMPLE PIECE 006", None,
         "Weekend combo offer — Sample Store 1 & Sample Store 2", "Sample Staff 25"),
    ]
    made = []
    for days, field, op, value, rnd, filters, text, ids, note, who in plan:
        try:
            rev = price_svc.apply(db, field, op, value, filters=filters, text=text,
                                  ids=ids, round_to=rnd, note=note, user=who)
        except price_svc.PricingError as exc:
            db.rollback()
            print("price change skipped:", note[:40], "—", exc)
            continue
        rev.created_at = ago(days, hours=4, minutes=random.randint(0, 59))
        db.commit()
        made.append((days, rev))
    # one put back: the price-point change, undone two days later
    for days, rev in made:
        if rev.note and rev.note.startswith("Price point"):
            try:
                price_svc.revert(db, rev, user="superadmin")
                rev.reverted_at = ago(days - 2, hours=5)
                rev.note = rev.note + " (reverted — store asked to hold old price)"
                db.commit()
            except price_svc.PricingError as exc:
                db.rollback()
                print("revert skipped:", exc)
    return len(made)


# ---------------------------------------------------------------------------
#  6a. Stock audit — the shelf check (presence) and its sessions
# ---------------------------------------------------------------------------
def _bundle_location(db, product_id, wid):
    for b in (db.query(models.Bundle).join(models.Purchase,
                                           models.Bundle.purchase_id == models.Purchase.id)
              .filter(models.Purchase.warehouse_id == wid, models.Bundle.location.isnot(None))
              .order_by(models.Bundle.id.desc()).all()):
        if any(p.id == product_id for p in b.products):
            return b.location
    return None


def _stock_audits(db, whs):
    plan = [  # (days ago, warehouse, scans, close?, note)
        (40, "WH-A", 18, True, "Monthly shelf check — men's section"),
        (33, "WH-C", 9, True, "Sample shelf check — hall 1"),
        (26, "WH-B", 15, True, "Racks K & M"),
        (12, "WH-A", 20, True, "Ladies section after rack re-layout"),
        (5, "WH-C", 8, True, "First floor spot check"),
        (0, "WH-B", 7, False, "Weekly check — in progress"),
    ]
    products = db.query(models.Product).order_by(models.Product.id).all()
    made = 0
    for days, code, n, close, note in plan:
        wh = whs.get(code)
        if not wh:
            continue
        who = random.choice(COUNTERS)
        start = ago(days, hours=3 if days else 1, minutes=random.randint(0, 40))
        sess = sa_svc.open_session(db, wh.id, by=who, note=note)
        db.flush()
        here = [p for p in products if stock_loc.qty_at(db, p.id, wh.id) > 0]
        away = [p for p in products if stock_loc.qty_at(db, p.id, wh.id) <= 0
                and (p.stock_qty or 0) > 0]
        random.shuffle(here)
        random.shuffle(away)
        codes = [p.sku for p in here[:max(1, n - 3)]]
        codes += [p.sku for p in away[:2]]
        codes.append(random.choice(["OLD-TAG-0457", "8901234567890", "VND-55120", "ESSA-09911"]))
        unit = (db.query(models.ProductUnit)
                .filter(models.ProductUnit.product_id.in_([p.id for p in here[:5]] or [0]))
                .first())
        if unit:
            codes.insert(2, unit.code)
        codes = [c for c in codes if c][:n + 1]
        t = start
        for i, c in enumerate(codes):
            res = sa_svc.scan(db, sess, c, by=who)
            row = res["scan"]
            t += dt.timedelta(seconds=random.randint(25, 140))
            row.scanned_at = row.last_seen_at = t
            if row.product_id:
                row.location = _bundle_location(db, row.product_id, wh.id) or row.location
            if i % 7 == 3:                    # read twice — counted once
                again = sa_svc.scan(db, sess, c, by=who)["scan"]
                again.last_seen_at = t + dt.timedelta(seconds=40)
        sess.started_at = start
        if close:
            sa_svc.close_session(db, sess, by=who)
            sess.closed_at = t + dt.timedelta(minutes=random.randint(3, 15))
        db.commit()
        made += 1
    return made


# ---------------------------------------------------------------------------
#  6b. Physical stock audit — the counted stocktake
# ---------------------------------------------------------------------------
def _best_brand(db, wid, lo=4, hi=14):
    opts = pa_svc.filter_options(db, wid).get("brand") or []
    best = None
    for b in opts:
        n = len(pa_svc.candidates(db, wid, {"brand": b}))
        if lo <= n <= hi and (best is None or n > best[1]):
            best = (b, n)
    if best is None and opts:
        best = (opts[0], 0)
    return best[0] if best else None


def _physical_audits(db, whs):
    # (days ago, warehouse, final status, counted share, note, reason)
    plan = [
        (50, "WH-A", "applied", 1.0, "Quarterly count — selected brand",
         "Quarterly stocktake variance — approved by warehouse head"),
        (32, "WH-C", "reviewed", 1.0, "Sample count — section C", None),
        (18, "WH-B", "completed", 1.0, "Count after transfer from WH-A", None),
        (9, "WH-A", "cancelled", 0.0, "Opened over the wrong rack — recounting", None),
        (1, "WH-B", "counting", 0.5, "Monthly count — in progress", None),
    ]
    made = 0
    used_brands = {}
    for days, code, final, share, note, reason in plan:
        wh = whs.get(code)
        if not wh:
            continue
        brand = _best_brand(db, wh.id)
        if final == "counting" and used_brands.get(code) == brand:
            opts = [b for b in (pa_svc.filter_options(db, wh.id).get("brand") or []) if b != brand]
            brand = opts[0] if opts else brand
        used_brands[code] = brand
        filters = {"brand": brand} if brand else {}
        if final == "counting":
            filters["remove_sales"] = True       # shown on the open count's header
        who = random.choice(COUNTERS)
        try:
            audit = pa_svc.open_audit(db, wh.id, by=who, note=note, filters=filters)
        except pa_svc.AuditError as exc:
            db.rollback()
            print("physical audit skipped:", exc)
            continue
        db.flush()
        start = ago(days, hours=2, minutes=15)
        audit.started_at = start
        t = start + dt.timedelta(minutes=20)
        lines = list(audit.lines)
        to_count = lines[:max(0, round(len(lines) * share))]
        for i, line in enumerate(to_count):
            sysq = float(stock_loc.qty_at(db, line.product_id, wh.id) or 0)
            qty = sysq
            if i % 5 == 2:
                qty = max(0, sysq - random.choice([1, 2]))       # short
            elif i % 7 == 4:
                qty = sysq + 1                                   # excess
            pa_svc.record(db, audit, line, qty, by=who,
                          note=line.note)
            t += dt.timedelta(minutes=random.randint(2, 7))
            line.counted_at = t
        steps = {"applied": ["completed", "reviewed", "approved"],
                 "reviewed": ["completed", "reviewed"], "completed": ["completed"],
                 "cancelled": ["cancelled"], "counting": []}[final]
        for k, st in enumerate(steps):
            by = who if st == "completed" else random.choice(MANAGERS)
            pa_svc.set_status(db, audit, st, by=by)
            stamp = pa_svc.STATUS_STAMPS.get(st)
            if stamp:
                setattr(audit, stamp[0], t + dt.timedelta(hours=1 + k * 20))
        if final == "applied":
            audit.change_reason = reason
            pa_svc.apply(db, audit, by="superadmin", reason=reason)
            audit.applied_at = (audit.approved_at or t) + dt.timedelta(hours=2)
            db.flush()
            _restamp(db, ("physical_audit",), audit.id, audit.applied_at)
        db.commit()
        made += 1
    return made


# ---------------------------------------------------------------------------
#  7. Labels — templates and print history
# ---------------------------------------------------------------------------
LABELS = [
    ("Price Tag 38 × 25 mm", "Small swing tag — name, size, MRP and QR.",
     38, 25, "product", True, [
         {"field": "product_name", "x": 1.5, "y": 1.2, "w": 35, "h": 3.6, "size": 6.5, "bold": True},
         {"field": "size", "x": 1.5, "y": 5.2, "w": 14, "h": 3.4, "size": 7, "bold": True, "prefix": "Size "},
         {"field": "sku", "x": 1.5, "y": 9, "w": 20, "h": 3.2, "size": 6, "font": '"Courier New", monospace'},
         {"field": "mrp", "x": 1.5, "y": 14, "w": 20, "h": 5, "size": 9, "bold": True, "prefix": "MRP "},
         {"field": "qr_code", "x": 22, "y": 7, "w": 15, "h": 15}]),
    ("Premium Tag 60 × 40 mm", "Larger tag — design no, colour, MRP and offer price.",
     60, 40, "product", True, [
         {"field": "company_name", "x": 2, "y": 1.5, "w": 56, "h": 4, "size": 8, "bold": True, "align": "center"},
         {"field": "custom_text", "x": 2, "y": 5.8, "w": 56, "h": 3.4, "size": 6.5, "align": "center",
          "text": "Sample Custom Text", "italic": True},
         {"field": "product_name", "x": 2, "y": 10, "w": 34, "h": 4.4, "size": 8, "bold": True},
         {"field": "design_no", "x": 2, "y": 15, "w": 34, "h": 3.6, "size": 7, "prefix": "Design "},
         {"field": "color", "x": 2, "y": 19, "w": 34, "h": 3.6, "size": 7},
         {"field": "line", "x": 2, "y": 23.5, "w": 34, "h": 0.3, "color": "#999999"},
         {"field": "mrp", "x": 2, "y": 25, "w": 34, "h": 5, "size": 11, "bold": True, "prefix": "MRP "},
         {"field": "sale_price", "x": 2, "y": 31, "w": 34, "h": 4, "size": 8, "prefix": "Offer "},
         {"field": "qr_code", "x": 38, "y": 12, "w": 20, "h": 20}]),
    ("Piece Label 50 × 25 mm", "One per garment — carries the piece code, so returns and audits know which piece.",
     50, 25, "unit", True, [
         {"field": "piece_code", "x": 2, "y": 1.5, "w": 28, "h": 3.6, "size": 7, "bold": True,
          "font": '"Courier New", monospace'},
         {"field": "product_name", "x": 2, "y": 6, "w": 28, "h": 4, "size": 6.5},
         {"field": "size", "x": 2, "y": 11, "w": 12, "h": 3.4, "size": 7, "bold": True, "prefix": "Size "},
         {"field": "mrp", "x": 2, "y": 16, "w": 26, "h": 5, "size": 9, "bold": True, "prefix": "MRP "},
         {"field": "qr_code", "x": 30, "y": 3, "w": 18, "h": 18}]),
    ("Carton / Bin Label 100 × 50 mm", "Rack and carton strip — GRN, supplier, received date and batch.",
     100, 50, "product", True, [
         {"field": "company_name", "x": 3, "y": 2, "w": 60, "h": 5, "size": 10, "bold": True},
         {"field": "product_name", "x": 3, "y": 8, "w": 60, "h": 5, "size": 10, "bold": True},
         {"field": "grn_no", "x": 3, "y": 15, "w": 60, "h": 4, "size": 8, "prefix": "GRN "},
         {"field": "supplier", "x": 3, "y": 20, "w": 60, "h": 4, "size": 8},
         {"field": "received_date", "x": 3, "y": 25, "w": 60, "h": 4, "size": 8, "prefix": "Received "},
         {"field": "batch", "x": 3, "y": 30, "w": 60, "h": 4, "size": 8, "prefix": "Batch "},
         {"field": "qr_code", "x": 66, "y": 8, "w": 30, "h": 30}]),
    ("Old Barcode Tag 50 × 25 (retired)", "The 1D barcode tag used before QR labels. Kept for reprints of old stock.",
     50, 25, "product", False, [
         {"field": "product_name", "x": 2, "y": 1.5, "w": 46, "h": 4, "size": 7, "bold": True},
         {"field": "barcode", "x": 2, "y": 7, "w": 46, "h": 10},
         {"field": "mrp", "x": 2, "y": 18.5, "w": 30, "h": 5, "size": 9, "bold": True, "prefix": "MRP "}]),
]


def _labels(db):
    ld.ensure_default(db)
    for t in db.query(models.LabelTemplate).all():
        t.created_at = ago(120)
    for i, (name, desc, w, h, target, active, elements) in enumerate(LABELS):
        if db.query(models.LabelTemplate).filter(models.LabelTemplate.name == name).first():
            continue
        db.add(models.LabelTemplate(
            name=name, description=desc, width_mm=w, height_mm=h, padding_mm=1.5,
            border=True, target=target, font="Arial, Helvetica, sans-serif",
            elements=ld.normalise_elements(elements, w, h), active=active,
            is_default=False, created_by=random.choice(MANAGERS),
            created_at=ago(100 - i * 17, hours=5)))
    db.commit()

    # print history: pieces of items that were tagged, some printed twice
    units = (db.query(models.ProductUnit).join(models.Bundle,
                                               models.ProductUnit.bundle_id == models.Bundle.id)
             .filter(models.Bundle.status == "tagged").all())
    if not units:
        units = db.query(models.ProductUnit).order_by(models.ProductUnit.id).limit(300).all()
    by_product = {}
    for u in units:
        by_product.setdefault(u.product_id, []).append(u)
    for k, (pid, us) in enumerate(sorted(by_product.items())):
        if k % 4 == 3:
            continue                     # never printed — "Print" rather than "Reprint"
        who = random.choice(COUNTERS)
        unit_svc.mark_printed(db, us, by=who)
        when = ago(random.randint(3, 40), hours=random.randint(1, 8))
        for n, u in enumerate(us):
            if k % 5 == 0 and n < 2:     # a torn tag, printed again
                unit_svc.mark_printed(db, [u], by=who)
            u.last_printed_at = when
    db.commit()
    return db.query(models.LabelTemplate).count()


# ---------------------------------------------------------------------------
#  5. Dead stock & clearance worksheets
# ---------------------------------------------------------------------------
def _clearance(db):
    rows = [r for r in ds.product_rows(db) if r["status"] != "healthy"]
    rows.sort(key=lambda r: (-r["days_idle"], -r["stock_value"]))
    dead = [r for r in rows if r["status"] in ("dead", "critical")]
    near = [r for r in rows if r["status"] == "approaching"]
    plans = [
        ("Sample Clearance 1", "closed", 62, 60, 30, dead[-4:] or near[:3],
         ["Markdown", "Clear Now", "Promotional Sale"],
         "Markdown on slow sizes across WH-A and WH-B stores."),
        ("Sample Markdown 2", "active", 6, 5, -25, dead[:8],
         ["Clear Now", "Markdown", "Bundle", "Transfer to Store", "Markdown"],
         "Ladder discounts on 90+ day stock; move remaining pieces to Sample Store 1 and Sample Store 3."),
        ("Sample Clearance 3", "draft", 1, -3, -33, near[:6],
         ["Return to Supplier", "Bundle", "Hold", "Review"],
         "Approaching-dead items: ask suppliers to take back, or bundle as combos."),
    ]
    made = 0
    for name, status, created, starts, ends, picked, actions, note in plans:
        if not picked:
            continue
        c = models.ClearanceCampaign(
            name=name, status=status, note=note, created_by=random.choice(MANAGERS),
            starts_on=(TODAY - dt.timedelta(days=starts)).date().isoformat(),
            ends_on=(TODAY - dt.timedelta(days=ends)).date().isoformat(),
            created_at=ago(created, hours=4))
        if status == "closed":
            c.closed_at = ago(ends, hours=9)
        db.add(c)
        db.flush()
        for i, r in enumerate(picked):
            line = models.ClearanceLine(campaign_id=c.id,
                                        **ds.line_from_row(r, actions[i % len(actions)]))
            line.added_at = c.created_at + dt.timedelta(minutes=5 + i)
            if status == "active" and i == 1:
                line.discount_pct = float(line.discount_pct or 0) + 10
                base = float(line.mrp or line.cost_price or 0)
                line.clearance_price = round(base * (1 - line.discount_pct / 100.0), 2)
                line.expected_realisation = round(line.clearance_price * float(line.qty or 0), 2)
                line.note = "Extra 10% approved for one week"
            db.add(line)
        made += 1
    db.commit()
    return made


if __name__ == "__main__":
    session = SessionLocal()
    try:
        run(session)
    finally:
        session.close()
