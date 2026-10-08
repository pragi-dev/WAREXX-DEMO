"""Build the static demo's STORE / POS data in a scratch shop database.

    set ESSA_DATABASE_URL=sqlite:///C:/path/to/demo-build/demo.db
    set DATABASE_URL=sqlite:///C:/path/to/demo-build/shop.db
    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\seed_shop.py

ENVIRONMENT
    DATABASE_URL       REQUIRED. The shop's own database (the "Textile Retail
                       Shop" Flask app). A sqlite:/// file inside a folder named
                       `demo-build`. It is DELETED and rebuilt from nothing.
    ESSA_DATABASE_URL  REQUIRED. The warehouse database that seed_base.py built
                       (Sample Company: 3 warehouses, 6 stores, their floors
                       and tills, the SAMPLE PIECE products). Stores are
                       matched by ORDER (the first six in the warehouse's
                       stores table), so their names do not matter here. A sqlite:/// file inside a
                       `demo-build` folder. Only ever READ — exactly as the
                       mounted shop reads it.
    ESSA_WAREHOUSE_DB  Optional. The same warehouse file as a plain path. The
                       shop stat()s it to notice warehouse changes; it is set
                       from ESSA_DATABASE_URL automatically when absent. Set it
                       on the recording server too (see below).

WHAT IT DOES
The mounted /pos (backend/app/pos_mount.load_pos_app) builds the shop by running
create_all, then the shop's own syncs against the warehouse — master categories,
warehouse items, places (stores → floors → tills) and transfers — and seeds a
first user only if there are none. This script does the same thing in the same
order (with its own staff created first, so the shop's generic sample seed never
runs), and then trades ~60 days through the shop's own HTTP endpoints via the
Flask test client — /pos/checkout for every counter bill (bill numbers, stock,
loyalty, promotions, coupons, split tenders all done by the shop itself),
/pos/invoice/<id>/cancel, /returns/create, /alterations/create, the floor-sales
session flow — and through its services for audits, drawers and wishes.
Afterwards the timestamps are moved back to the simulated day each document
belongs to. Only deliveries, attendance, advances and feedback are written as
rows directly (their screens have no bulk path).

Dates are relative to the moment this runs and "today" is the local calendar
day, so record the demo the same day the shop DB is built.

LOGGING IN (the shop has its own login, even when mounted):
    shop admin   admin / admin123   ("Sample Admin", owner)
    managers     staff01, staff02 / staff123
    store staff  staff03 .. staff16 / staff123   (see STAFF below)

All names are generic placeholders: Sample Customer NNN, Sample Staff NN,
Sample Tailor N, Sample Offer N, ABC Area / Sample City, +91 90000 0xxxx phones,
sampleNNN@example.com. Set SHOP_NAME="Sample Company" on the recording server
so the shop's header reads that rather than the generic "Store".

It refuses to touch any database that is not clearly a scratch file.
"""
import datetime as dt
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SHOP_DIR = REPO / "app" / "Textile Retail Shop"


def _sqlite_path(url):
    return Path(url[len("sqlite:///"):]) if url.startswith("sqlite:///") else None


def _guard():
    shop_url = os.environ.get("DATABASE_URL", "").strip()
    wh_url = os.environ.get("ESSA_DATABASE_URL", "").strip()
    paths = {}
    for name, url in (("DATABASE_URL", shop_url), ("ESSA_DATABASE_URL", wh_url)):
        p = _sqlite_path(url)
        if p is None or "demo-build" not in [part.lower() for part in p.resolve().parts]:
            sys.exit(f"Refusing: {name} must be a sqlite:/// file inside a folder named "
                     f"'demo-build' (got {url!r}). This script DELETES the shop database.")
        paths[name] = p.resolve()
    if paths["DATABASE_URL"] == paths["ESSA_DATABASE_URL"]:
        sys.exit("Refusing: DATABASE_URL and ESSA_DATABASE_URL are the same file.")
    real = {(SHOP_DIR / "textile_shop.db").resolve(), (REPO / "app" / "backend" / "data" / "essa.db").resolve()}
    if paths["DATABASE_URL"] in real or paths["ESSA_DATABASE_URL"] in real:
        sys.exit("Refusing: that is a real database, not a demo-build scratch file.")
    if not paths["ESSA_DATABASE_URL"].is_file():
        sys.exit(f"Refusing: no warehouse database at {paths['ESSA_DATABASE_URL']} — run seed_base.py first.")
    return paths["DATABASE_URL"], paths["ESSA_DATABASE_URL"]


SHOP_DB, WH_DB = _guard()
os.environ.setdefault("ESSA_WAREHOUSE_DB", str(WH_DB))
# A fresh file: the shop's create_all builds it, as on a first mount.
for suffix in ("", "-wal", "-shm", "-journal"):
    f = Path(str(SHOP_DB) + suffix)
    if f.exists():
        f.unlink()

random.seed(7)
PLAN = random.Random(11)      # what is bought, by whom, where
RT = random.Random(23)        # decisions taken as the day unfolds

sys.path.insert(0, str(SHOP_DIR))
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from app import create_app, db                                     # noqa: E402  (the shop's)
from app import audits as audit_svc                                # noqa: E402
from app import drawer as drawer_svc                               # noqa: E402
from app import messaging, places, transfers                       # noqa: E402
from app import warehouse_items as wi                              # noqa: E402
from app.master_categories import sync_master_categories           # noqa: E402
from app.models import (                                           # noqa: E402
    Alteration, Attendance, Category, Company, Coupon, CouponCampaign, CouponRedemption,
    Counter, CreditNote, Customer, CustomerAdvance, CustomerFeedback, Delivery,
    DeliveryBill, DeliveryLine, DeliveryScan, DrawerSession, Floor, Invoice, Location,
    LocationStock, Product, PromotionApplication, PromotionCondition, PromotionConditionItem, PromotionPlace,
    PromotionReward, PromotionRewardItem, PromotionScheme, SaleSession, StaffAdvance,
    StaffAdvanceRecovery, StockAudit, StockMovement, Tailor, User)

NOW = dt.datetime.now().replace(microsecond=0)
TODAY = NOW.date()
DAYS = 60                                   # days of history before today

COMPANY = dict(name="Sample Company Pvt Ltd", gstin="33AAACS0000A1Z5",
               address="1, ABC Area, Main Road, Sample City",
               state_code="33", phone="+91 90000 00001")

# The six stores, in the warehouse's own order (seed_base.py STORES): key, bills a
# day, silk showroom? Names, cities and floors are read from the warehouse.
STORES = [("S1", 13, False), ("S2", 9, False), ("S3", 12, False),
          ("S4", 7, False), ("S5", 6, True), ("S6", 7, True)]
STORE_NAME = {}                     # key -> the warehouse's store name (filled by _places)
STORE_CITY = {}                     # key -> the warehouse's store city
AREAS = ["ABC Area", "PQR Area", "XYZ Area", "DEF Area", "LMN Area"]

# (username, full name, role, store key or None, salary, commission %)
STAFF = [("admin", "Sample Admin", "admin", None, 0, 0),
         ("staff01", "Sample Staff 01", "manager", None, 42000, 0.5),
         ("staff02", "Sample Staff 02", "manager", None, 40000, 0.5)]
for _n, (_store, _comm) in enumerate([("S1", 1.0), ("S1", 1.5), ("S1", 1.5), ("S2", 1.0), ("S2", 1.5),
                                      ("S3", 1.0), ("S3", 1.5), ("S3", 1.5), ("S4", 1.0), ("S4", 1.5),
                                      ("S5", 1.0), ("S5", 1.0), ("S6", 1.0), ("S6", 1.0), ("S2", 1.0)], 3):
    STAFF.append((f"staff{_n:02d}", f"Sample Staff {_n:02d}", "cashier", _store,
                  15000 + 500 * (_n % 8), _comm))
INACTIVE = "staff17"                # the last one has left
PASSWORDS = {"admin": "admin123"}


# ---------------------------------------------------------------------------
#  small helpers
# ---------------------------------------------------------------------------

def say(*a):
    print("[seed_shop]", *a, flush=True)


def at(day, hh, mm=0, ss=0):
    return dt.datetime.combine(day, dt.time(hh, mm, ss))


def bill_times(day, n):
    """n wall-clock moments in trading hours on `day`, sorted. Today stops at now."""
    start, end = at(day, 10, 15), at(day, 21, 15)
    if day == TODAY:
        end = min(end, NOW - dt.timedelta(minutes=6))
        start = min(start, end - dt.timedelta(hours=2))
        start = max(start, at(day, 0, 5))
    span = max(60, int((end - start).total_seconds()))
    # evenings are busier: square-root skew towards the close
    return sorted(start + dt.timedelta(seconds=int(span * (PLAN.random() ** 0.8))) for _ in range(n))


def ok(resp, what):
    if resp.status_code >= 400:
        raise RuntimeError(f"{what}: HTTP {resp.status_code} {resp.get_data(as_text=True)[:400]}")
    return resp


# ---------------------------------------------------------------------------
#  the build
# ---------------------------------------------------------------------------

def main():
    app = create_app()
    app.config["TESTING"] = True
    # Requests below are made with NO app context held open: Flask reuses an
    # active one for a request, and with it `g`, where Flask-Login caches the
    # signed-in user — every till would then bill as whoever logged in first.
    with app.app_context():
        db.create_all()
        from app import dbpatch
        dbpatch.apply_all()
        _staff()
        _company()
        _mount_syncs()
        locs, floors, tills = _places()
        products = _products(floors)
        schemes = _promotions(locs)
        _coupon_campaign()
        customers = _customers()
        _tailors()

    plan = _plan_bills(tills, products, customers)
    with app.app_context():
        _opening_stock(plan, locs, products)
    clients = _clients(app, tills)
    dates = _trade(app, clients, plan, schemes)
    _floor_sales(app, products, customers, dates)
    with app.app_context():
        _backdate_sales(dates)
    _returns(app, clients, locs)
    _alterations(app, clients, locs)
    with app.app_context():
        _deliveries(locs)
        _audits(floors)
        _drawers(tills)
        _attendance()
        _staff_advances()
        _customer_extras(locs, tills)
        _finish(schemes)
        _report()
        db.session.commit()
        db.session.execute(db.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        db.session.commit()
    say("done:", SHOP_DB)


def U(username):
    """A shop user, read in the current app context."""
    return User.query.filter_by(username=username).first()


def staff_ids(code):
    return [U(u).id for u, _, _, s, *_ in STAFF if s == code and u != INACTIVE]


def staff_names(code):
    return [u for u, _, _, s, *_ in STAFF if s == code and u != INACTIVE]


# ---- people and places -----------------------------------------------------

def _staff():
    for i, (username, name, role, store, salary, comm) in enumerate(STAFF):
        u = User(username=username, full_name=name, role=role, salary=salary,
                 commission_pct=comm, active=username != INACTIVE,
                 phone=f"+91 90000 000{i + 10:02d}",
                 email=f"sample.{username}@example.com",
                 created_at=dt.datetime.combine(TODAY - dt.timedelta(days=200 - i * 3), dt.time(10)))
        u.set_password(PASSWORDS.get(username, "staff123"))
        db.session.add(u)
    db.session.commit()


def _company():
    db.session.add(Company(is_default=True, active=True, **COMPANY))
    db.session.commit()


def _mount_syncs():
    """What backend/app/pos_mount.load_pos_app runs on every start, in its order."""
    sync_master_categories()
    seen = wi._warehouse_signature()
    r = wi.sync_warehouse_items()
    wi._last_signature = seen
    places.sync_locations()
    transfers.sync_transfers()
    say("warehouse sync:", r, "| locations", Location.query.count(),
        "floors", Floor.query.count(), "tills", Counter.query.count())


def _places():
    """Plain data: {code: {id, name}}, {code: [floor id]}, {code: [{id, location_id, name}]}."""
    con = wi._connect()
    try:
        rows = con.execute("SELECT name, city FROM stores ORDER BY id").fetchall()
    finally:
        con.close()
    if len(rows) < len(STORES):
        sys.exit(f"The warehouse has {len(rows)} stores, not {len(STORES)} — is it a seed_base build?")
    locs = {}
    for (key, _, _), row in zip(STORES, rows):
        loc = Location.query.filter_by(name=row["name"]).first()
        if loc is None:
            sys.exit(f"The shop did not take in the store {row['name']!r} from the warehouse.")
        locs[key] = {"id": loc.id, "name": loc.name}
        STORE_NAME[key], STORE_CITY[key] = loc.name, row.get("city") or "Sample City"
    # Every branch gets a "Counter 1" from sync_locations before its real tills
    # arrive from the warehouse. These stores all have warehouse tills, so the
    # stand-in is switched off — it would only show up as an unassigned counter.
    for c in Counter.query.filter(Counter.wh_id.is_(None)).all():
        if Counter.query.filter(Counter.location_id == c.location_id, Counter.wh_id.isnot(None)).count():
            c.active = False
    db.session.commit()
    floors = {code: [f.id for f in Floor.query.filter_by(location_id=loc["id"], active=True)
                     .order_by(Floor.sort_order).all()] for code, loc in locs.items()}
    tills = {code: [{"id": t.id, "location_id": t.location_id, "name": t.name}
                    for t in Counter.query.filter_by(location_id=loc["id"], active=True)
                    .filter(Counter.wh_id.isnot(None)).order_by(Counter.wh_id).all()]
             for code, loc in locs.items()}
    for code in locs:
        if len(floors[code]) < 1 or not tills[code]:
            sys.exit(f"Store {code} came across without floors or tills — is the warehouse a seed_base build?")
    return locs, floors, tills


def _gst(p):
    """GST as a textile shop charges it: fabric and sarees 5%, garments 5% up to
    ₹2,500 a piece and 18% above (the 2025 rate change)."""
    hsn = (p.hsn_code or "")[:2]
    if hsn in ("50", "52", "54"):
        return 5.0
    return 5.0 if (p.selling_price or 0) <= 2500 else 18.0


def _is_silk(cat, desc=None):
    """Silk-showroom lines, by category (descriptions are placeholders)."""
    return cat in ("PATTU PAVADAI", "MENS-DHOTI SET", "LADIES-SAREE")


def _products(floors):
    """Shop-side attributes the warehouse does not carry — GST, reorder level and
    which floor of which store each line is racked on — returned as plain data."""
    prods = Product.query.order_by(Product.id).all()
    if len(prods) < 20:
        sys.exit(f"Only {len(prods)} products came across from the warehouse — is it a seed_base build?")
    fl = lambda code, i: floors[code][min(i, len(floors[code]) - 1)]          # noqa: E731
    silk_floor = [fl("S5", 0), fl("S6", 1), fl("S5", 1), fl("S6", 0)]
    garment_floor = {"ESSA MENS-T-SHIRT": fl("S3", 0), "MENS-SHIRT": fl("S1", 0),
                     "MENS-PANT BIT": fl("S1", 0), "JERKIN": fl("S4", 0),
                     "LADIES-CHUDITHAR": fl("S1", 1), "LADIES-T-SHIRT": fl("S2", 0),
                     "LADIES-AARAM": fl("S2", 0), "KIDS-MIDI": fl("S3", 1),
                     "KIDS-T-SHIRT": fl("S3", 1), "MENS-KURTA SET": fl("S4", 0)}
    out = []
    for i, p in enumerate(prods):
        p.gst_rate = _gst(p)
        cat = p.category.name if p.category else ""
        p.reorder_level = 2 if (p.selling_price or 0) > 4000 else 6
        if _is_silk(cat, p.description):
            p.floor_id = silk_floor[i % len(silk_floor)]
        else:
            p.floor_id = garment_floor.get(cat, fl("S2", 0))
        out.append({"id": p.id, "price": float(p.selling_price or 0), "gst": p.gst_rate,
                    "cat": cat, "desc": p.description or "", "sku": p.sku})
    db.session.commit()
    return out


def _cat(name):
    c = Category.query.filter_by(name=name).first()
    if c is None:
        sys.exit(f"Category {name!r} is not in the master — backend/data/categories.json missing?")
    return c


#: (code, name, type, priority, (from days-ago, to days-ago or None), buy, get,
#:  reward value, stores, terms, max per bill, active at the end)
SCHEMES = [
    ("OFFER-1", "Sample Offer 1", "buy_get_free", 10, (60, 41),
     [(2, ["LADIES-CHUDITHAR"])], [(1, ["LADIES-AARAM"])], 0.0, "garment",
     "Sample terms: buy any two LADIES-CHUDITHAR, get one LADIES-AARAM free.", 1, True),
    ("OFFER-2", "Sample Offer 2", "buy_get_percent", 20, (26, None),
     [(2, ["KIDS-T-SHIRT"])], [(1, ["KIDS-MIDI"])], 50.0, "garment",
     "Sample terms: two KIDS-T-SHIRT earn one KIDS-MIDI at 50% off.", None, True),
    ("OFFER-3", "Sample Offer 3", "buy_get_price", 30, (16, None),
     [(2, ["MENS-PANT BIT"])], [(1, ["ESSA MENS-T-SHIRT"])], 199.0, "garment",
     "Sample terms: two MENS-PANT BIT earn one ESSA MENS-T-SHIRT at ₹199.", None, True),
    ("OFFER-4", "Sample Offer 4", "buy_get_percent", 15, (34, None),
     [(1, ["LADIES-SAREE"])], [(1, ["PATTU PAVADAI"])], 20.0, "silk",
     "Sample terms: one LADIES-SAREE earns one PATTU PAVADAI at 20% off.", 1, True),
    ("OFFER-5", "Sample Offer 5", "buy_get_percent", 5, (-12, -30),
     [(2, ["LADIES-SAREE"])], [(1, ["MENS-DHOTI SET"])], 30.0, "silk",
     "Sample terms: two LADIES-SAREE earn one MENS-DHOTI SET at 30% off. Scheduled.", None, True),
    ("OFFER-6", "Sample Offer 6", "buy_get_percent", 40, (55, 20),
     [(1, ["JERKIN"])], [(1, ["JERKIN"])], 40.0, "garment",
     "Sample terms: switched off after its run.", None, False),
]
SCHEME_END = {"OFFER-2": 21, "OFFER-3": 30, "OFFER-4": 45}


def _promotions(locs):
    admin = U("admin")
    where = {"garment": [locs[c]["id"] for c in ("S1", "S2", "S3", "S4")],
             "silk": [locs[c]["id"] for c in ("S5", "S6")]}
    out = []
    for (code, name, kind, prio, window, buy, gets, value, stores, terms, max_apps, active) in SCHEMES:
        s = PromotionScheme(code=code, name=name, scheme_type=kind, priority=prio,
                            max_applications=max_apps, reward_selection="cheapest",
                            out_of_stock="substitute", return_policy="reclaim_value",
                            terms=terms, created_by_id=admin.id, updated_by_id=admin.id,
                            created_at=at(TODAY - dt.timedelta(days=max(window[0], 0) + 3), 11),
                            active=False)
        db.session.add(s)
        db.session.flush()
        for n, (qty, cats) in enumerate(buy):
            c = PromotionCondition(scheme_id=s.id, min_qty=qty, sort_order=n)
            db.session.add(c)
            db.session.flush()
            for cn in cats:
                db.session.add(PromotionConditionItem(condition_id=c.id, category_id=_cat(cn).id))
        for n, (qty, cats) in enumerate(gets):
            r = PromotionReward(scheme_id=s.id, qty=qty, value=value, sort_order=n)
            db.session.add(r)
            db.session.flush()
            for cn in cats:
                db.session.add(PromotionRewardItem(reward_id=r.id, category_id=_cat(cn).id))
        for loc_id in where[stores]:
            db.session.add(PromotionPlace(scheme_id=s.id, location_id=loc_id))
        out.append({"code": code, "window": window, "active": active})
    db.session.commit()
    return out


def _coupon_campaign():
    admin = U("admin")
    db.session.add(CouponCampaign(name="Sample Coupon Campaign 1", kind="amount", value=250, min_bill=2000,
                                  valid_days=45, uses_per_coupon=1, issue_at_settlement=True,
                                  settlement_min_bill=10000, active=True, created_by_id=admin.id,
                                  created_at=at(TODAY - dt.timedelta(days=DAYS + 2), 12)))
    db.session.add(CouponCampaign(name="Sample Coupon Campaign 2", kind="percent", value=10,
                                  max_discount=1000, min_bill=5000, valid_days=60,
                                  uses_per_coupon=1, issue_at_settlement=False, active=True,
                                  created_by_id=admin.id,
                                  created_at=at(TODAY - dt.timedelta(days=30), 12)))
    db.session.commit()


def _customers():
    """Placeholder customers, each with a home store. Returns {store key: [id]}."""
    out = defaultdict(list)
    weights = {"S1": 95, "S2": 70, "S3": 85, "S4": 55, "S5": 50, "S6": 65}
    n = 0
    for code, count in weights.items():
        for _ in range(count):
            n += 1
            joined = dt.datetime.combine(TODAY - dt.timedelta(days=PLAN.randint(70, 900)),
                                         dt.time(PLAN.randint(10, 20), PLAN.randint(0, 59)))
            dob = dt.date(PLAN.randint(1962, 2002), PLAN.randint(1, 12), PLAN.randint(1, 28))
            if n % 23 == 0:              # a few birthdays this week, for the wishes queue
                d = TODAY + dt.timedelta(days=n % 6)
                dob = dt.date(PLAN.randint(1970, 1998), d.month, min(d.day, 28))
            has_dob = PLAN.random() < 0.7 or n % 23 == 0
            c = Customer(name=f"Sample Customer {n:03d}", phone=f"+91 90000 0{1000 + n:04d}",
                         email=f"sample{n:03d}@example.com" if PLAN.random() < 0.55 else "",
                         address=f"{PLAN.randint(2, 180)}, {PLAN.choice(AREAS)}, {STORE_CITY[code]}",
                         state_code="33", dob=dob if has_dob else None,
                         anniversary=(dt.date(PLAN.randint(1990, 2022), PLAN.randint(1, 12), PLAN.randint(1, 28))
                                      if PLAN.random() < 0.35 else None),
                         created_at=joined)
            db.session.add(c)
            out[code].append(c)
    # five business customers (two from other states, so IGST bills exist)
    for k, (code, sc) in enumerate([("S1", "33"), ("S3", "33"), ("S6", "33"), ("S6", "29"), ("S5", "32")], 1):
        n += 1
        c = Customer(name=f"Sample Customer {n:03d} (B2B)", gstin=f"{sc}AABCS{9000 + k}A1Z{k}",
                     state_code=sc, phone=f"+91 90000 0{1000 + n:04d}", email=f"sample{n:03d}@example.com",
                     address=f"{10 + k}, ABC Area, " + ("Sample City" if sc == "33" else "Other State City"),
                     created_at=at(TODAY - dt.timedelta(days=300), 12))
        db.session.add(c)
        out[code].insert(0, c)       # a regular: top of the store's list
    db.session.commit()
    return {k: [c.id for c in v] for k, v in out.items()}


def _tailors():
    for n, note in enumerate(["Sample notes: blouses and falls", "Sample notes: hems and fitting",
                              "Sample notes: silk blouse stitching", "Sample notes: bridal blouse work"], 1):
        db.session.add(Tailor(name=f"Sample Tailor {n}", phone=f"+91 90000 00{50 + n:03d}", notes=note,
                              active=True, created_at=at(TODAY - dt.timedelta(days=120), 11)))
    db.session.add(Tailor(name="Sample Tailor 5", phone="+91 90000 00055", notes="Closed", active=False))
    db.session.commit()


# ---- what will be sold -----------------------------------------------------

def _assortment(products):
    """Which products each kind of store sells, with a popularity weight."""
    garment, silk = [], []
    for p in products:
        cat, price = p["cat"], p["price"]
        if _is_silk(cat, p["desc"]):
            silk.append((p["id"], 3.0 if price < 6000 else 1.3))
            if price < 6000:
                garment.append((p["id"], 0.5))
        elif cat in ("LADIES-CHUDITHAR", "MENS-KURTA SET"):
            silk.append((p["id"], 1.4))
            garment.append((p["id"], 2.2))
        else:
            garment.append((p["id"], 2.5 if price < 800 else 1.8))
    return {"garment": garment, "silk": silk}


def _plan_bills(tills, products, customers):
    """Every counter bill of the period, decided up front so opening stock can
    be set to cover it."""
    staff_of = {code: [u for u, _, _, s, *_ in STAFF if s == code and u != INACTIVE]
                for code, _, _ in STORES}
    assort = _assortment(products)
    pmap = {p["id"]: p for p in products}
    bills = []
    for back in range(DAYS, -1, -1):
        day = TODAY - dt.timedelta(days=back)
        weekend = day.weekday() >= 5
        festive = 1.25 if back in range(18, 24) else 1.0        # a festival week
        for code, per_day, silk in STORES:
            n = per_day * (1.45 if weekend else 1.0) * festive * PLAN.uniform(0.75, 1.25)
            if day == TODAY:
                hours = max(0.0, (NOW - at(day, 10, 15)).total_seconds() / 3600.0)
                n = max(3, n * min(1.0, hours / 11.0))
            n = int(round(n))
            pool = assort["silk" if silk else "garment"]
            ids, w = [p for p, _ in pool], [x for _, x in pool]
            cust_ids = customers[code]
            cw = [1.0 / ((i + 1) ** 0.85) for i in range(len(cust_ids))]
            for t in bill_times(day, n):
                ti = PLAN.randrange(len(tills[code]))
                lines = {}
                k = (PLAN.choices([1, 2, 3], weights=[60, 30, 10])[0] if silk else
                     PLAN.choices([1, 2, 3, 4], weights=[45, 30, 17, 8])[0])
                for pid in PLAN.choices(ids, weights=w, k=k):
                    q = 1 if pmap[pid]["price"] > 3000 else PLAN.choice([1, 1, 1, 2, 2, 3])
                    lines[pid] = lines.get(pid, 0) + q
                if not silk and PLAN.random() < 0.10:
                    # some carts carry a qualifying pair — which is why offers exist
                    want = PLAN.choice(["KIDS-T-SHIRT", "MENS-PANT BIT", "LADIES-CHUDITHAR"])
                    pick = [pid for pid in ids if pmap[pid]["cat"] == want]
                    if pick:
                        pid = PLAN.choice(pick)
                        lines[pid] = max(lines.get(pid, 0), 2)
                walk_in = PLAN.random() < (0.30 if silk else 0.38)
                cust = None if walk_in else PLAN.choices(cust_ids, weights=cw)[0]
                pay = PLAN.choices(["cash", "upi", "card", "split"],
                                   weights=[15, 30, 45, 10] if silk else [30, 38, 25, 7])[0]
                discount = (PLAN.choice([0, 0, 0, 0, 50, 100, 200]) if silk else
                            PLAN.choice([0] * 12 + [20, 50, 100]))
                bills.append(dict(when=t, day=day, store=code, till=tills[code][ti]["id"],
                                  cashier=staff_of[code][ti % len(staff_of[code])],
                                  staff=PLAN.choice(staff_of[code]), customer=cust,
                                  lines=sorted(lines.items()), pay=pay, discount=discount,
                                  redeem=PLAN.random() < 0.18, coupon=PLAN.random() < 0.6,
                                  cancel=PLAN.random() < (0.05 if back == 0 else 0.004)))
    bills.sort(key=lambda b: b["when"])
    say(f"{len(bills)} counter bills planned")
    return bills


REWARD_CATS = {"LADIES-AARAM", "KIDS-MIDI", "ESSA MENS-T-SHIRT", "PATTU PAVADAI", "MENS-DHOTI SET",
               "JERKIN"}


def _opening_stock(plan, locs, products):
    """Stock dispatched to each store, enough to cover what it sells plus what
    is still on its shelves today. A handful of lines are left nearly sold out,
    so the dashboard's low-stock list has something real in it."""
    sold = defaultdict(float)                 # (store, product) -> qty
    for b in plan:
        for pid, q in b["lines"]:
            sold[(b["store"], pid)] += q
    by_product = defaultdict(float)
    for (s, pid), q in sold.items():
        by_product[pid] += q
    ranked = sorted((p for p in products if p["cat"] not in REWARD_CATS),
                    key=lambda p: -by_product.get(p["id"], 0))
    low = {p["id"] for p in ranked[3:45:5]}    # every fifth best-seller runs short
    cat_of = {p["id"]: p["cat"] for p in products}
    cat_sold = defaultdict(float)
    for (s, pid), q in sold.items():
        cat_sold[(s, cat_of[pid])] += q
    cheapest = {}
    for p in sorted(products, key=lambda p: (p["price"], p["id"])):
        cheapest.setdefault(p["cat"], p["id"])
    StockMovement.query.delete()               # the warehouse total the import opened with
    LocationStock.query.delete()
    total = defaultdict(float)
    tranche_days = [DAYS + 8, 38, 14]
    n = 0
    for code, loc in locs.items():
        for p in products:
            pid = p["id"]
            q = sold.get((code, pid), 0.0)
            reward = p["cat"] in REWARD_CATS
            if not q and not reward:
                continue
            if pid in low:
                top = max(locs, key=lambda c: sold.get((c, pid), 0))
                extra = 1.0 if code == top else 0.0
            else:
                extra = PLAN.randint(4, 14) if q else PLAN.randint(6, 12)
            if reward:                           # free and part-price goods go out too
                extra += 18
                if pid == cheapest.get(p["cat"]):    # the one the offers hand out first
                    extra += round(0.8 * cat_sold.get((code, p["cat"]), 0))
            if pid not in low and code in ("S1", "S3", "S2", "S4"):
                extra += 2                       # for the floor-sales phones
            qty = q + extra
            transfers.at(loc["id"], pid).qty = qty
            total[pid] += qty
            parts = [round(qty * 0.5), round(qty * 0.3)]
            parts.append(qty - sum(parts))
            for days_ago, part in zip(tranche_days, parts):
                if part <= 0:
                    continue
                n += 1
                db.session.add(StockMovement(
                    product_id=pid, change=part, reason="transfer-in",
                    reference=f"STO-{1000 + n} → {loc['name']}",
                    created_at=at(TODAY - dt.timedelta(days=days_ago), 8, 30)))
    for p in Product.query.all():
        p.stock_qty = total.get(p.id, 0.0)
    db.session.commit()
    say("opening stock", int(sum(total.values())), "pieces;", len(low), "lines left to run short")


# ---- trading through the shop's own endpoints ------------------------------

def _login(app, username):
    c = app.test_client()
    r = c.post("/login", data={"username": username,
                               "password": PASSWORDS.get(username, "staff123")})
    if r.status_code != 302:
        raise RuntimeError(f"could not sign {username} in to the shop")
    return c


def _clients(app, tills):
    """One signed-in till per counter, each standing at its own store."""
    out = {}
    for code, ts in tills.items():
        names = staff_names(code)
        for i, till in enumerate(ts):
            c = _login(app, names[i % len(names)])
            r = ok(c.post("/pos/place", json={"location_id": till["location_id"],
                                              "counter_id": till["id"]}), "place").get_json()
            if not (r.get("counter") and r["counter"]["id"] == till["id"]):
                raise RuntimeError(f"till {till['name']} did not take: {r}")
            out[till["id"]] = c
    out["manager"] = _login(app, "staff01")
    return out


def _price_total(lines, pmap, discount, redeem):
    sub = sum(q * pmap[pid]["price"] for pid, q in lines)
    tax = sum(round(q * pmap[pid]["price"] * pmap[pid]["gst"] / 100.0, 2) for pid, q in lines)
    return round(sub - min(discount, sub) + tax - redeem, 2)


def _tenders(kind, total):
    total = round(float(total), 2)
    ref = f"{RT.randint(1000, 9999)}"
    if kind == "card":
        return [{"method": "card", "amount": total, "reference": f"XXXX-{ref}"}]
    if kind == "upi":
        return [{"method": "upi", "amount": total, "reference": f"UPI/{RT.randint(10**11, 10**12 - 1)}"}]
    cash = float(min(total, max(100, round(total * RT.uniform(0.2, 0.6), -2))))
    rest = round(total - cash, 2)
    out = [{"method": "cash", "amount": cash, "tendered": cash}]
    if rest > 0:
        out.append({"method": "card", "amount": rest, "reference": f"XXXX-{ref}"})
    return out


def _set_schemes(app, schemes, day):
    """Run each offer on the days it ran. The engine reads date.today(), so the
    validity is opened up and the ACTIVE flag carries the window while trading;
    _finish puts the real dates back."""
    back = (TODAY - day).days
    with app.app_context():
        for s in schemes:
            row = PromotionScheme.query.filter_by(code=s["code"]).first()
            frm, to = s["window"]
            row.start_date = row.end_date = None
            row.active = back <= frm and (to is None or back >= to)
        db.session.commit()


def _trade(app, clients, plan, schemes):
    say("trading", len(plan), "bills through /pos/checkout …")
    with app.app_context():
        pmap = {p.id: {"price": float(p.selling_price), "gst": p.gst_rate} for p in Product.query.all()}
        points = {c.id: 0.0 for c in Customer.query.all()}
    coupons_held = defaultdict(list)            # customer id -> [(code, issued day)]
    dates = {}                                  # invoice id -> datetime
    current_day = None
    failures = 0
    t0 = dt.datetime.now()
    for i, b in enumerate(plan):
        if b["day"] != current_day:
            current_day = b["day"]
            _set_schemes(app, schemes, current_day)
        payload = {"items": [{"product_id": pid, "quantity": q} for pid, q in b["lines"]],
                   "staff_code": b["staff"],          # the username, as typed at a till
                   "discount": b["discount"]}
        redeem = 0.0
        cid = b["customer"]
        if cid:
            payload["customer_id"] = cid
            if b["redeem"] and points.get(cid, 0) >= 60:
                redeem = float(int(min(points[cid], 400)))
                payload["redeem_points"] = redeem
            held = [c for c in coupons_held[cid] if c[1] < b["day"]]
            if held and b["coupon"]:
                payload["coupon_code"] = held[0][0]
                coupons_held[cid].remove(held[0])
        if b["pay"] in ("split", "card", "upi"):
            payload["payments"] = _tenders(b["pay"], _price_total(b["lines"], pmap, b["discount"], redeem))
        else:
            payload["payment_method"] = b["pay"]
        client = clients[b["till"]]
        r, data = None, {}
        for _attempt in range(3):
            r = client.post("/pos/checkout", json=payload)
            data = r.get_json(silent=True) or {}
            if r.status_code < 400 and data.get("success"):
                break
            if r.status_code == 400 and "total" in data and "payments" in payload:
                payload["payments"] = _tenders(b["pay"], data["total"])      # promotions moved it
                continue
            if "coupon_code" in payload:
                payload.pop("coupon_code")
                continue
            break
        if r.status_code >= 400 or not data.get("success"):
            failures += 1
            if failures <= 5:
                say("checkout refused:", r.status_code, data)
            continue
        iid = data["invoice_id"]
        dates[iid] = b["when"]
        if cid:
            for c in data.get("coupons_issued") or []:
                coupons_held[cid].append((c["code"], b["day"]))
        if b["cancel"]:
            ok(clients["manager"].post(f"/pos/invoice/{iid}/cancel", data={
                "reason": RT.choice(["Customer changed mind before paying out",
                                     "Billed on the wrong customer", "Duplicate scan — rebilled"])}),
               "cancel")
        if cid:
            with app.app_context():
                points[cid] = db.session.get(Customer, cid).loyalty_points or 0.0
        if (i + 1) % 500 == 0:
            rate = (dt.datetime.now() - t0).total_seconds() / (i + 1)
            say(f"  {i + 1} bills … {rate * 1000:.0f} ms each")
    say(f"trade done: {len(dates)} bills, {failures} refused")
    return dates


def _floor_sales(app, products, customers, dates):
    """The phone-cart flow: finished sales over the last fortnight, and two carts
    still open on the admin's phone so Floor Sales has something live."""
    say("floor sales")
    with app.app_context():
        cheap = [p.id for p in Product.query.filter(Product.stock_qty > 8).order_by(Product.id).all()
                 if (p.selling_price or 0) < 2500 and not _is_silk(p.category.name if p.category else "",
                                                                   p.description)]
    home = {"staff04": "S1", "staff05": "S1", "staff09": "S3", "staff10": "S3",
            "staff07": "S2", "staff12": "S4"}
    finished = []
    for k, uname in enumerate(["staff04", "staff05", "staff09", "staff10", "staff07", "staff12",
                               "staff04", "staff09", "staff05", "staff10"]):
        c = _login(app, uname)
        r = ok(c.post("/floor/new"), "floor new")
        code = r.headers["Location"].rstrip("/").split("/")[-1]
        ok(c.post(f"/floor/s/{code}/update", json={
            "customer_id": RT.choice(customers[home[uname]][:30]),
            "payment_method": RT.choice(["upi", "cash", "card"])}), "floor update")
        for pid in RT.sample(cheap, RT.randint(1, 3)):
            ok(c.post(f"/floor/s/{code}/add", json={"product_id": pid}), "floor add")
        ok(c.post(f"/floor/s/{code}/request-approval"), "approval")
        ok(c.post(f"/floor/view/{code}/approve"), "approve")
        data = ok(c.post(f"/floor/s/{code}/finalize"), "finalize").get_json()
        when = at(TODAY - dt.timedelta(days=13 - k), RT.randint(11, 19), RT.randint(0, 59))
        dates[data["invoice_id"]] = when
        finished.append((code, when))
    adm = _login(app, "admin")
    for status, cust_ix in (("open", 3), ("awaiting", 7)):
        r = ok(adm.post("/floor/new"), "floor new")
        code = r.headers["Location"].rstrip("/").split("/")[-1]
        ok(adm.post(f"/floor/s/{code}/update", json={"customer_id": customers["S1"][cust_ix],
                                                     "payment_method": "upi"}), "update")
        for pid in RT.sample(cheap, 2 if status == "open" else 3):
            ok(adm.post(f"/floor/s/{code}/add", json={"product_id": pid}), "add")
        if status == "awaiting":
            ok(adm.post(f"/floor/s/{code}/request-approval"), "approval")
    with app.app_context():
        for code, when in finished:
            s = SaleSession.query.filter_by(code=code).first()
            s.created_at = when - dt.timedelta(minutes=9)
            s.updated_at = when
        for s in SaleSession.query.filter(SaleSession.status != "completed").all():
            s.created_at = NOW - dt.timedelta(minutes=RT.randint(8, 40))
            s.updated_at = NOW - dt.timedelta(minutes=RT.randint(1, 7))
        db.session.commit()


def _backdate_sales(dates):
    """Move every document a bill wrote to the moment the bill was rung."""
    say("backdating", len(dates), "bills")
    conn = db.session.connection()
    conn.execute(db.text("UPDATE invoices SET invoice_date = :d WHERE id = :i"),
                 [{"i": iid, "d": when} for iid, when in dates.items()])
    conn.execute(db.text(
        "UPDATE invoices SET cancelled_at = datetime(invoice_date, '+' || (abs(random()) % 50 + 4) || ' minutes') "
        "WHERE payment_status = 'cancelled'"))
    for table in ("invoice_payments", "loyalty_txns", "promotion_applications", "promotion_audit",
                  "coupon_redemptions"):
        conn.execute(db.text(
            f"UPDATE {table} SET created_at = (SELECT invoice_date FROM invoices "
            f"WHERE invoices.id = {table}.invoice_id) WHERE invoice_id IS NOT NULL"))
    conn.execute(db.text(
        "UPDATE loyalty_txns SET created_at = (SELECT cancelled_at FROM invoices "
        "WHERE invoices.id = loyalty_txns.invoice_id) WHERE reason LIKE 'cancel %'"))
    conn.execute(db.text(
        "UPDATE promotion_audit SET created_at = (SELECT cancelled_at FROM invoices "
        "WHERE invoices.id = promotion_audit.invoice_id) WHERE event = 'reversed'"))
    conn.execute(db.text(
        "UPDATE coupon_redemptions SET voided_at = (SELECT cancelled_at FROM invoices "
        "WHERE invoices.id = coupon_redemptions.invoice_id) WHERE voided_at IS NOT NULL"))
    # stock movements carry the bill number (and "@ store") as their reference
    num = dict(conn.execute(db.text("SELECT invoice_number, invoice_date FROM invoices")).all())
    cxl = dict(conn.execute(db.text(
        "SELECT invoice_number, cancelled_at FROM invoices WHERE cancelled_at IS NOT NULL")).all())
    upd = []
    for mid, ref, reason in conn.execute(db.text(
            "SELECT id, reference, reason FROM stock_movements "
            "WHERE reason IN ('sale', 'promo', 'cancel')")).all():
        key = (ref or "").split(" @ ")[0]
        when = (cxl if reason == "cancel" else num).get(key)
        if when:
            upd.append({"i": mid, "d": when})
    if upd:
        conn.execute(db.text("UPDATE stock_movements SET created_at = :d WHERE id = :i"), upd)
    # coupons a bill earned: valid from the day it was raised
    for cid, issued, days in conn.execute(db.text(
            "SELECT c.id, i.invoice_date, k.valid_days FROM coupons c "
            "JOIN invoices i ON i.id = c.issued_invoice_id "
            "JOIN coupon_campaigns k ON k.id = c.campaign_id")).all():
        issued = issued if isinstance(issued, dt.datetime) else dt.datetime.fromisoformat(str(issued))
        conn.execute(db.text("UPDATE coupons SET created_at = :c, valid_from = :f, valid_to = :t "
                             "WHERE id = :i"),
                     {"c": issued, "f": issued.date(),
                      "t": issued.date() + dt.timedelta(days=(days or 30) - 1), "i": cid})
    # a customer is on the books no later than their first bill
    conn.execute(db.text(
        "UPDATE customers SET created_at = (SELECT min(invoice_date) FROM invoices "
        "WHERE customer_id = customers.id) "
        "WHERE (SELECT min(invoice_date) FROM invoices WHERE customer_id = customers.id) < created_at"))
    db.session.commit()
    # deterministic coupon codes (the shop mints random ones)
    for n, c in enumerate(Coupon.query.order_by(Coupon.id).all(), 1):
        c.code = f"SAMPLE{n:04d}"
    db.session.commit()


def _till_for(clients, tills_by_loc, loc_id):
    return clients[tills_by_loc[loc_id]]


def _returns(app, clients, locs):
    """Goods back against real bills, through /returns/create."""
    say("returns")
    code_of = {l["id"]: c for c, l in locs.items()}
    reasons = ["Size too small — customer exchanging next visit", "Colour not as expected in daylight",
               "Gift duplicate", "Stitching defect at the seam", "Customer changed mind",
               "Fit issue at the waist"]
    with app.app_context():
        till_of = {}
        for k in clients:
            if isinstance(k, int):
                till_of.setdefault(db.session.get(Counter, k).location_id, k)
        cands = (Invoice.query.filter(Invoice.live(), Invoice.location_id.isnot(None),
                                      Invoice.invoice_date < NOW - dt.timedelta(days=2))
                 .order_by(Invoice.id).all())
        picks = []
        for inv in RT.sample(cands, min(48, len(cands))):
            items = [i for i in inv.items if not i.promo_role]
            if items:
                item = RT.choice(items)
                picks.append((inv.id, item.id, inv.location_id, inv.invoice_date))
        staff = {code: staff_ids(code) for code in locs}
    made = []
    for inv_id, item_id, loc_id, inv_date in picks:
        damaged = RT.random() < 0.15
        form = {"invoice_id": inv_id, "staff_code": RT.choice(staff[code_of[loc_id]]),
                f"qty_{item_id}": 1, f"cond_{item_id}": "damaged" if damaged else "resellable",
                "refund_method": RT.choice(["cash", "upi", "store_credit", "card", "cash"]),
                "reason": "Damaged — written off" if damaged else RT.choice(reasons)}
        r = clients[till_of[loc_id]].post("/returns/create", data=form)
        loc = r.headers.get("Location", "")
        if r.status_code != 302 or "/returns/" not in loc or not loc.rstrip("/").split("/")[-1].isdigit():
            continue
        when = min(inv_date + dt.timedelta(days=RT.randint(1, 7), hours=RT.randint(0, 5)),
                   NOW - dt.timedelta(hours=1))
        made.append((int(loc.rstrip("/").split("/")[-1]), when))
    with app.app_context():
        conn = db.session.connection()
        for nid, when in made:
            number = conn.execute(db.text("SELECT number FROM credit_notes WHERE id = :i"), {"i": nid}).scalar()
            conn.execute(db.text("UPDATE credit_notes SET created_at = :d WHERE id = :i"), {"d": when, "i": nid})
            conn.execute(db.text("UPDATE stock_movements SET created_at = :d WHERE reference = :r"),
                         {"d": when, "r": number})
            conn.execute(db.text("UPDATE loyalty_txns SET created_at = :d WHERE reason = :r"),
                         {"d": when, "r": f"return {number}"})
        db.session.commit()
    say(len(made), "credit notes")


ALTER = {"MENS-PANT BIT": ["Shorten hem by 1.5 inches", "Hem to 31 inch length, keep original finish"],
         "MENS-SHIRT": ["Take in sides by 1 inch", "Shorten sleeves by 1 inch"],
         "LADIES-CHUDITHAR": ["Take in waist, ease armhole", "Shorten kurti by 2 inches, add side slits"],
         "MENS-KURTA SET": ["Shorten kurta by 2 inches", "Taper pyjama at ankle"],
         "LADIES-SAREE": ["Blouse stitching with lining, 3/4 sleeves", "Fall and pico, tassels on pallu",
                          "Princess-cut blouse, back hooks"],
         "PATTU PAVADAI": ["Shorten pavadai by 2 inches, add elastic"],
         "MENS-DHOTI SET": ["Velcro waist on dhoti"]}


def _alterations(app, clients, locs):
    say("alterations")
    code_of = {l["id"]: c for c, l in locs.items()}
    with app.app_context():
        tailors = [t.id for t in Tailor.query.filter_by(active=True).order_by(Tailor.id).all()]
        till_of = {}
        for k in clients:
            if isinstance(k, int):
                till_of.setdefault(db.session.get(Counter, k).location_id, k)
        recent = (Invoice.query.filter(Invoice.live(), Invoice.location_id.isnot(None),
                                       Invoice.customer_id.isnot(None),
                                       Invoice.invoice_date >= NOW - dt.timedelta(days=24))
                  .order_by(Invoice.id).all())
        RT.shuffle(recent)
        picks = []
        for inv in recent:
            if len(picks) >= 36:
                break
            items = [i for i in inv.items if not i.promo_role and i.product.category
                     and i.product.category.name in ALTER]
            if items:
                picks.append((inv.id, items[0].id, items[0].product.category.name,
                              inv.location_id, inv.invoice_date))
        staff = {code: staff_ids(code) for code in locs}
    jobs = []
    for inv_id, item_id, cat, loc_id, inv_date in picks:
        code = code_of[loc_id]
        silk = code in ("S5", "S6")
        taken = inv_date + dt.timedelta(minutes=RT.randint(5, 30))
        promised = (taken + dt.timedelta(days=RT.choice([1, 2, 3, 4, 5]))).date()
        tailor = tailors[2] if code == "S5" else tailors[3] if code == "S6" else tailors[RT.choice([0, 1])]
        form = {"invoice_id": inv_id, "staff_code": RT.choice(staff[code]), "tailor_id": tailor,
                "promised_date": promised.isoformat(),
                "charge": RT.choice([350, 450, 650, 0]) if silk else RT.choice([0, 0, 0, 150, 250]),
                "remarks": RT.choice(["", "Customer will collect after 6 pm", "Call before delivery",
                                      "Urgent — function on Sunday"]),
                f"qty_{item_id}": 1, f"note_{item_id}": RT.choice(ALTER[cat])}
        r = clients[till_of[loc_id]].post("/alterations/create", data=form)
        loc = r.headers.get("Location", "")
        if r.status_code != 302 or not loc.rstrip("/").split("/")[-1].isdigit():
            continue
        jobs.append((int(loc.rstrip("/").split("/")[-1]), taken, promised))
    with app.app_context():
        for jid, taken, promised in jobs:
            job = db.session.get(Alteration, jid)
            job.created_at = taken
            if promised < TODAY - dt.timedelta(days=1) and RT.random() < 0.85:
                job.status = "delivered"
                job.ready_at = at(promised, RT.randint(11, 17), RT.randint(0, 59))
                job.delivered_at = min(job.ready_at + dt.timedelta(days=RT.randint(0, 2), hours=2),
                                       NOW - dt.timedelta(hours=2))
                job.delivered_by_id = job.staff_id
                if job.charge:
                    job.charge_method = RT.choice(["cash", "upi"])
            elif promised <= TODAY and RT.random() < 0.6:
                job.status = "ready"
                job.ready_at = min(at(promised, 12, 30), NOW - dt.timedelta(hours=1))
            # otherwise still with the tailor — some of them overdue, which the list shows
        db.session.commit()
    say(len(jobs), "alteration jobs")


def _deliveries(locs):
    """The delivery desk handing goods over against nearly every bill. The last
    few days keep some bills waiting and a few part-collected."""
    say("deliveries")
    code_of = {l["id"]: c for c, l in locs.items()}
    staff = {code: staff_ids(code) for code in locs}
    override_by = U("staff01").id
    invoices = Invoice.query.filter(Invoice.live()).order_by(Invoice.invoice_date, Invoice.id).all()
    n = Delivery.query.count()
    pending = part = 0
    for inv in invoices:
        if not inv.items:
            continue
        recent = inv.invoice_date >= NOW - dt.timedelta(days=4)
        roll = RT.random()
        if recent and roll < 0.10:
            pending += 1
            continue                                   # waiting at the desk
        partial = recent and roll < 0.15 and any(i.quantity >= 2 for i in inv.items)
        who = RT.choice(staff[code_of.get(inv.location_id, "S1")])
        n += 1
        note = Delivery(number=f"DLV-{n:06d}", customer_id=inv.customer_id, staff_id=who,
                        cashier_id=who, company_id=inv.company_id, location_id=inv.location_id,
                        counter_id=inv.counter_id,
                        created_at=min(inv.invoice_date + dt.timedelta(minutes=RT.randint(3, 25)),
                                       NOW - dt.timedelta(minutes=1)))
        db.session.add(note)
        db.session.flush()
        db.session.add(DeliveryBill(delivery_id=note.id, invoice_id=inv.id))
        done_partial = False
        for item in inv.items:
            qty = item.pending_qty
            if qty <= 0:
                continue
            if partial and not done_partial and qty >= 2:
                qty -= 1
                done_partial = True
            scanned = qty if RT.random() > 0.03 else max(0.0, qty - 1)
            line = DeliveryLine(delivery_id=note.id, invoice_item_id=item.id, product_id=item.product_id,
                                quantity=qty, scanned=scanned,
                                override_reason=None if scanned == qty else "Tag missing — matched by design no.",
                                overridden_by_id=None if scanned == qty else override_by)
            db.session.add(line)
            db.session.flush()
            for _ in range(int(scanned)):
                db.session.add(DeliveryScan(delivery_line_id=line.id, code=item.product.sku,
                                            created_at=note.created_at))
        part += done_partial
        if n % 400 == 0:
            db.session.commit()
    db.session.commit()
    say(n, "deliveries;", pending, "bills waiting;", part, "part-collected")


def _audits(floors):
    """Physical counts through the shop's audit service: history on several
    floors, two approved and applied, and one being counted right now."""
    say("audits")
    kav, sen, adm = U("staff01"), U("staff02"), U("admin")
    fl = lambda code, i: db.session.get(Floor, floors[code][min(i, len(floors[code]) - 1)])  # noqa: E731
    plan = [  # (floor, days ago, final status, counted by)
        (fl("S1", 0), 41, "adjusted", kav), (fl("S3", 1), 33, "approved", kav),
        (fl("S5", 0), 27, "adjusted", sen), (fl("S2", 0), 19, "reviewed", kav),
        (fl("S6", 1), 12, "completed", sen), (fl("S4", 0), 8, "cancelled", kav),
        (fl("S1", 1), 5, "approved", kav), (fl("S3", 0), 0, "in_progress", U("staff09")),
    ]
    for floor, back, final, who in plan:
        audit = audit_svc.open_audit(floor, user_id=who.id,
                                     note=f"{'Quarterly' if back > 25 else 'Monthly'} count — {floor.name}")
        start = at(TODAY - dt.timedelta(days=back), 8, 15) if back else NOW - dt.timedelta(hours=1, minutes=20)
        lines = list(audit.lines)
        upto = len(lines) if final != "in_progress" else max(1, int(len(lines) * 0.55))
        if final == "cancelled":
            upto = min(2, len(lines))
        for k, line in enumerate(lines[:upto]):
            sysq = line.system_qty or 0
            roll = RT.random()
            gap = 0 if roll < 0.62 else (-RT.choice([1, 1, 2, 3]) if roll < 0.9 else RT.choice([1, 2]))
            audit_svc.count(audit, line, max(0.0, sysq + gap), user_id=who.id,
                            note="Found in the trial room" if gap > 0 else ("Not on the rack" if gap else None))
            line.counted_at = start + dt.timedelta(minutes=6 * (k + 1))
            line.scans = 1 + (k % 3 == 0)
        db.session.commit()
        steps = {"completed": ["completed"], "reviewed": ["completed", "reviewed"],
                 "approved": ["completed", "reviewed", "approved"],
                 "adjusted": ["completed", "reviewed", "approved"],
                 "cancelled": ["cancelled"], "in_progress": []}[final]
        for step in steps:
            if step == "completed" and not audit.counted_lines:
                break
            by = adm if step == "approved" else sen if step == "reviewed" else who
            audit_svc.set_status(audit, step, user_id=by.id)
        if final == "adjusted" and audit.can_adjust:
            audit_svc.apply_adjustment(audit, user_id=adm.id)
            StockMovement.query.filter(StockMovement.reference.like(f"{audit.number} @%")).update(
                {"created_at": start + dt.timedelta(days=2, hours=3)}, synchronize_session=False)
        audit.started_at = start
        if audit.completed_at:
            audit.completed_at = start + dt.timedelta(hours=2, minutes=40)
        if audit.reviewed_at:
            audit.reviewed_at = start + dt.timedelta(days=1, hours=2)
        if audit.approved_at:
            audit.approved_at = start + dt.timedelta(days=2, hours=1)
        if audit.adjusted_at:
            audit.adjusted_at = start + dt.timedelta(days=2, hours=3)
        db.session.commit()


def _drawers(tills):
    """Yesterday's drawers counted and closed; today's open since the morning."""
    say("drawers")
    yday = TODAY - dt.timedelta(days=1)
    company = Company.query.filter_by(is_default=True).first()
    admin_id = U("admin").id
    for code, ts in tills.items():
        for till in ts:
            last = Invoice.query.filter_by(counter_id=till["id"]).order_by(Invoice.id.desc()).first()
            uid = last.cashier_id if last else admin_id
            s = DrawerSession(company_id=company.id, location_id=till["location_id"], counter_id=till["id"],
                              opened_by_id=uid, opened_at=at(yday, 9, 50), opening_float=5000.0)
            db.session.add(s)
            db.session.flush()
            close = at(yday, 21, 35)
            expected, _ = drawer_svc.breakdown(s, until=close)
            s.closed_at, s.closed_by_id, s.expected_cash = close, uid, expected
            s.counted_cash = round(expected + RT.choice([0, 0, 0, -10, 20, -50]), 2)
            if s.counted_cash != expected:
                s.notes = "Short/over noted in the register"
            db.session.add(DrawerSession(company_id=company.id, location_id=till["location_id"],
                                         counter_id=till["id"], opened_by_id=uid,
                                         opened_at=min(at(TODAY, 9, 50), NOW - dt.timedelta(minutes=30)),
                                         opening_float=5000.0))
    db.session.commit()


def _attendance():
    """A month of shifts: in around half past nine, out after closing, one day off a week."""
    say("attendance")
    Attendance.query.delete()                # the sign-ins this script made
    for k, (username, *_rest) in enumerate(STAFF):
        u = U(username)
        if not u.active:
            continue
        off = k % 7
        for back in range(30, -1, -1):
            day = TODAY - dt.timedelta(days=back)
            if day.weekday() == off and username != "admin":
                continue
            if RT.random() < 0.03:
                continue                         # on leave
            cin = at(day, 9, RT.randint(10, 58))
            if day == TODAY:
                if cin < NOW:
                    db.session.add(Attendance(user_id=u.id, check_in=cin))
                continue
            cout = at(day, 20, 30) + dt.timedelta(minutes=RT.randint(0, 70))
            db.session.add(Attendance(user_id=u.id, check_in=cin, check_out=cout,
                                      notes="Half day" if RT.random() < 0.03 else None))
    db.session.commit()


def _staff_advances():
    say("staff advances")
    adm = U("admin")
    for uname, amt, back, recovered in [("staff12", 5000, 48, [2500, 2500]), ("staff10", 3000, 25, [1500]),
                                        ("staff06", 8000, 18, [2000]), ("staff11", 2000, 6, [])]:
        a = StaffAdvance(user_id=U(uname).id, amount=amt, given_on=TODAY - dt.timedelta(days=back),
                         method="cash", note="Festival advance" if back < 30 else "Medical",
                         created_by_id=adm.id, created_at=at(TODAY - dt.timedelta(days=back), 18))
        db.session.add(a)
        db.session.flush()
        for j, r in enumerate(recovered):
            on = min(TODAY, TODAY - dt.timedelta(days=back) + dt.timedelta(days=20 * (j + 1)))
            db.session.add(StaffAdvanceRecovery(advance_id=a.id, amount=r, method="salary",
                                                recovered_on=on, note="Salary deduction",
                                                created_by_id=adm.id, created_at=at(on, 18)))
    db.session.commit()


def _customer_extras(locs, tills):
    """Bridal-order advances, feedback off the bill QR and the counter, and the
    birthday wishes queued for the coming week."""
    say("advances, feedback, wishes")
    silk = [(locs["S5"]["id"], tills["S5"][-1]["id"], "staff13"),
            (locs["S6"]["id"], tills["S6"][-1]["id"], "staff15")]
    custs = (Customer.query.filter(Customer.state_code == "33", Customer.gstin.is_(None))
             .order_by(Customer.total_spent.desc()).limit(6).all())
    company = Company.query.filter_by(is_default=True).first()
    notes = [f"Sample advance note {k}" for k in range(1, 7)]
    for k, c in enumerate(custs):
        loc_id, till_id, cashier = silk[k % 2]
        when = at(TODAY - dt.timedelta(days=3 + k * 4), 16, 20)
        a = CustomerAdvance(number=f"ADV-{k + 1:06d}", customer_id=c.id,
                            amount=[25000, 15000, 10000, 40000, 5000, 12000][k],
                            method=["upi", "card", "cash", "upi", "cash", "card"][k],
                            note=notes[k], cashier_id=U(cashier).id, company_id=company.id,
                            location_id=loc_id, counter_id=till_id, created_at=when)
        if k == 4:
            a.refunded, a.refund_method = 5000, "cash"
            a.refunded_at, a.refunded_by_id = when + dt.timedelta(days=2), U("staff02").id
        db.session.add(a)
    comments = {r: [f"Sample feedback {r}{x}" for x in "ab"] + [""] for r in (5, 4, 3, 2)}
    invs = Invoice.query.filter(Invoice.live(), Invoice.customer_id.isnot(None)).order_by(Invoice.id).all()
    for inv in RT.sample(invs, min(70, len(invs))):
        rating = RT.choices([5, 4, 3, 2], weights=[52, 31, 12, 5])[0]
        db.session.add(CustomerFeedback(customer_id=inv.customer_id, invoice_id=inv.id, rating=rating,
                                        comments=RT.choice(comments[rating]) or None,
                                        source=RT.choice(["link", "counter"]), recorded_by_id=inv.cashier_id,
                                        created_at=min(inv.invoice_date + dt.timedelta(hours=RT.randint(1, 30)),
                                                       NOW - dt.timedelta(minutes=5))))
    db.session.commit()
    messaging.queue_wishes("birthday", days_ahead=7, user_id=U("admin").id)
    messaging.queue_wishes("anniversary", days_ahead=7, user_id=U("admin").id)
    db.session.commit()


def _finish(schemes):
    """Promotions get their real validity back now the trading is done."""
    for s in schemes:
        row = PromotionScheme.query.filter_by(code=s["code"]).first()
        frm, to = s["window"]
        row.start_date = TODAY - dt.timedelta(days=frm)
        row.end_date = (TODAY - dt.timedelta(days=to)) if to is not None else \
            TODAY + dt.timedelta(days=SCHEME_END.get(s["code"], 30))
        row.active = s["active"]
        row.updated_at = row.created_at
    db.session.commit()


def _report():
    from sqlalchemy import func
    today_sales = db.session.query(func.coalesce(func.sum(Invoice.total), 0)).filter(
        Invoice.live(), func.date(Invoice.invoice_date) == TODAY.isoformat()).scalar()
    say({t: m.query.count() for t, m in [
        ("users", User), ("customers", Customer), ("products", Product), ("invoices", Invoice),
        ("credit_notes", CreditNote), ("alterations", Alteration), ("deliveries", Delivery),
        ("audits", StockAudit), ("promo_applications", PromotionApplication),
        ("coupons", Coupon), ("coupon_redemptions", CouponRedemption), ("attendance", Attendance)]})
    say("today's takings:", round(today_sales, 2), "| low-stock lines:",
        Product.query.filter(Product.stock_qty <= Product.reorder_level).count(),
        "| cancelled:", Invoice.query.filter_by(payment_status="cancelled").count())


if __name__ == "__main__":
    main()
