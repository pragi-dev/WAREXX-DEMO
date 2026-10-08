"""Admin, masters and activity for the static demo's SCRATCH database.

    set ESSA_DATABASE_URL=sqlite:///C:/path/to/demo-build/demo.db
    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\seed_admin.py

Runs LAST, after seed_base.py and the purchasing / operations seeders, and
works whether or not those ran: every row it builds on is looked up, never
assumed. It fills the screens nobody's day-to-day trade fills by itself:

  * Users & Access   a dozen accounts across the four roles, allotments, a
                     restricted receiving clerk, two people who have left
  * Audit Trail      ~80 events over the last 30 days, written in the same
                     sentences the auth middleware writes (services/audit),
                     drawn from the real GRNs / transfers / POs / LRs on file
  * Notifications    the derived queues get a believable "waiting since" and a
                     roster of who watches them
  * Masters          every ERP master and the "in use by this app" lists
  * Catalogues       full attribute vocabularies for Garments and Silks

The three start-up accounts (superadmin / admin / user) keep their default
passwords; only their display names change. Fictional data only.
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
             "folder. This script writes demo accounts and activity.")

random.seed(11)         # the same story on every build

from app import models                                             # noqa: E402
from app.config import BUSINESS_UTC_OFFSET_MINUTES                 # noqa: E402
from app.database import SessionLocal                              # noqa: E402
from app.security import required_access                           # noqa: E402
from app.services import audit as audit_svc                        # noqa: E402
from app.services import business_day                              # noqa: E402
from app.services import catalogues as cat_svc                     # noqa: E402
from app.services import masters as masters_svc                    # noqa: E402
from app.services import notifications as notif_svc                # noqa: E402
from app.services import unit_types as ut_svc                      # noqa: E402
from app.services import users as users_svc                        # noqa: E402
from app.services.figures import inr, qty as fmt_qty               # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
OFFSET = dt.timedelta(minutes=BUSINESS_UTC_OFFSET_MINUTES)
TODAY = business_day.today()                     # the business's own calendar day
WINDOW_START = NOW - dt.timedelta(days=30)


def local_at(days_ago, hh, mm=0):
    """A UTC instant for hh:mm on the business's calendar `days_ago` days back,
    never later than a few minutes ago."""
    day = TODAY - dt.timedelta(days=days_ago)
    utc = dt.datetime.combine(day, dt.time(hh, mm)) - OFFSET
    latest = NOW - dt.timedelta(minutes=4)
    if utc > latest:
        # today, later than now: pull it back into the part of the day that has happened
        start = dt.datetime.combine(TODAY, dt.time(8, 30)) - OFFSET
        span = max(60, int((latest - start).total_seconds()))
        utc = start + dt.timedelta(seconds=random.randint(0, span))
        utc = min(utc, latest)
    return utc


def work_time(days_ago):
    return local_at(days_ago, random.randint(9, 18), random.randint(0, 59))


# ===========================================================================
#  1 · Users & Access
# ===========================================================================
#: the start-up accounts: names only, passwords untouched
DEFAULT_NAMES = {"superadmin": "Sample Admin", "admin": "Sample Manager 1",
                 "user": "Sample User 01"}

# (username, password, role, full name, warehouse codes, active, created days ago,
#  extra permissions)
STAFF = [
    ("owner", "owner@123", "superboss", "Sample Owner", [], True, 170, None),
    ("manager2", "manager2@123", "admin", "Sample Manager 2", ["WH-A", "WH-B"], True, 160, None),
    ("manager3", "manager3@123", "admin", "Sample Manager 3", ["WH-C"], True, 150, None),
    ("user02", "user02@123", "user", "Sample User 02", ["WH-A"], True, 140, None),
    ("user03", "user03@123", "user", "Sample User 03", ["WH-B"], True, 120, None),
    ("user04", "user04@123", "user", "Sample User 04", ["WH-B"], True, 110, None),
    # a receiving clerk narrowed screen by screen, with cost prices withheld
    ("user05", "user05@123", "user", "Sample User 05", ["WH-A"], True, 60,
     {"screens": {"dashboard": ["view"], "lr": ["view", "create"],
                  "documents": ["view", "create"], "purchases": ["view", "create"],
                  "inventory": ["view"], "locator": ["view"], "labelprint": ["view", "print"]},
      "data": ["hide_cost_price"]}),
    ("user06", "user06@123", "user", "Sample User 06", ["WH-C"], False, 130, None),
    ("user07", "user07@123", "user", "Sample User 07", ["WH-A"], False, 165, None),
]


def _users(db, whs):
    users_svc.seed(db)                     # the start-up accounts, exactly as the server makes them
    for username, name in DEFAULT_NAMES.items():
        u = db.query(models.User).filter(models.User.username == username).first()
        if u:
            u.full_name = name
            u.created_at = u.created_at and min(u.created_at, NOW - dt.timedelta(days=180))
    db.commit()
    for username, pw, role, name, wh_codes, active, age, extra in STAFF:
        u = db.query(models.User).filter(models.User.username == username).first()
        if not u:
            u = users_svc.create_user(db, username, pw, role, full_name=name,
                                      created_by="owner" if role == "admin" else "superadmin")
        perms = dict(extra or {})
        ids = [whs[c].id for c in wh_codes if c in whs]
        if ids:
            perms["warehouses"] = ids
        u.permissions = perms or None
        u.active = active
        u.created_at = NOW - dt.timedelta(days=age, hours=random.randint(0, 8))
        if username == "owner":
            u.created_by = "system"
    db.commit()
    return {u.username: u for u in db.query(models.User).all()}


# ===========================================================================
#  2 · Audit Trail
# ===========================================================================
class Trail:
    """Events collected first, written in time order at the end — the trail
    reads newest-first BY ID, so the ids have to follow the clock."""

    def __init__(self, db, users):
        self.db, self.users, self.rows = db, users, []

    def add(self, when, username, method, path, summary, ref=None, warehouse_id=None,
            outcome="ok", status=None, screen=None, action=None):
        if when is None or when < WINDOW_START or when > NOW:
            return
        u = self.users.get(username)
        if screen is None or action is None:
            _need, scr, act = required_access(method, path)
            screen, action = screen or scr, action or act
        self.rows.append(dict(
            at=when, username=username, full_name=(u.full_name if u else None),
            role=(u.role if u else None), method=method, path=path, screen=screen,
            action=action, outcome=outcome,
            status=status or (200 if outcome == "ok" else 403),
            warehouse_id=warehouse_id, summary=summary, ref=ref))

    def described(self, when, username, method, path, fallback=None):
        """The sentence the middleware itself would write for this request."""
        _b, after, m = audit_svc.match(method, path)
        out = None
        if after is not None:
            try:
                out = after(self.db, m, None, username)
            except Exception:                              # noqa: BLE001
                out = None
        if not out or not out.get("summary"):
            if not fallback:
                _need, scr, act = required_access(method, path)
                fallback = {"summary": audit_svc.generic(scr, act, path)}
            out = fallback
        self.add(when, username, method, path, out["summary"], ref=out.get("ref"),
                 warehouse_id=out.get("warehouse_id"))

    def signin(self, when, username, outcome="ok"):
        summary = {"ok": "signed in",
                   "failed": "failed to sign in — wrong username or password",
                   "refused": "tried to sign in to a deactivated account"}[outcome]
        self.add(when, username, "POST", "/api/auth/login", summary, outcome=outcome,
                 status={"ok": 200, "failed": 401, "refused": 403}[outcome],
                 screen="session", action="signin")

    def write(self):
        """Every event on the trail — ours and any an earlier seeder wrote —
        renumbered in time order."""
        E = models.AuditEvent
        cols = [c.key for c in E.__table__.columns if c.key != "id"]
        old = [{c: getattr(e, c) for c in cols} for e in self.db.query(E).all()]
        wh_names = {w.id: w.name for w in self.db.query(models.Warehouse).all()}
        for r in self.rows:
            r["warehouse_name"] = wh_names.get(r["warehouse_id"])
            r["summary"] = (r["summary"] or "")[:500]
            r["ref"] = (r["ref"] or None) and str(r["ref"])[:80]
        every = sorted(old + self.rows, key=lambda r: r["at"] or NOW)
        self.db.query(E).delete(synchronize_session=False)
        self.db.flush()
        for i, r in enumerate(every, start=1):
            self.db.add(E(id=i, **{c: r.get(c) for c in cols}))
        self.db.commit()
        return len(self.rows)


def _staff_for(users, wid, whs, admin_ok=True, screen=None):
    """Active floor users allotted to a warehouse (or anyone unrestricted) who
    may work the screen in question."""
    pool = []
    for u in users.values():
        if not u.active or u.role not in ("user", "admin"):
            continue
        grants = (u.permissions or {}).get("screens")
        if grants and screen and screen not in grants:
            continue
        ids = (u.permissions or {}).get("warehouses") or []
        if u.role == "admin" and not admin_ok:
            continue
        if wid in ids or (not ids and u.username in ("user", "admin")):
            pool.append(u.username)
    return pool or ["user"]


def _parse_day(s, hh=11):
    try:
        d = dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None
    return dt.datetime.combine(d, dt.time(hh, random.randint(0, 59))) - OFFSET


def _audit(db, users, whs):
    t = Trail(db, users)
    wh_ids = {c: w.id for c, w in whs.items()}

    # --- GRNs actually on file -------------------------------------------------
    for p in db.query(models.Purchase).order_by(models.Purchase.id).all():
        who = random.choice(_staff_for(users, p.warehouse_id, whs, admin_ok=False,
                                       screen="purchases"))
        if p.document_id:
            t.described((p.created_at or NOW) - dt.timedelta(minutes=20), who, "POST",
                        f"/api/purchases/from-document/{p.document_id}")
        else:
            t.add(p.created_at, who, "POST", "/api/purchases",
                  f"added a GRN against invoice {p.invoice_number or '—'}",
                  ref=p.grn_no, warehouse_id=p.warehouse_id)
        if p.status == "posted" and p.posted_at:
            t.described(p.posted_at + dt.timedelta(minutes=random.randint(30, 150)), who,
                        "POST", f"/api/purchases/{p.id}/post")

    # --- transfers ---------------------------------------------------------------
    for o in db.query(models.StockOutward).order_by(models.StockOutward.id).all():
        packed = _parse_day(o.date, 10)
        who = random.choice(_staff_for(users, o.from_warehouse_id, whs, admin_ok=False,
                                       screen="outward"))
        if packed:
            t.add(packed, who, "POST", "/api/outward",
                  f"packed transfer {o.code or '#' + str(o.id)} to {o.to_destination or '—'}"
                  f" — {fmt_qty(o.total_qty)} units (draft)", ref=o.code,
                  warehouse_id=o.from_warehouse_id)
        if o.status in ("posted", "received") and packed:
            t.described(packed + dt.timedelta(minutes=random.randint(40, 120)), who,
                        "POST", f"/api/outward/{o.id}/post")
        if o.status == "received" and packed:
            rcv = random.choice(_staff_for(users, o.to_warehouse_id, whs, admin_ok=False,
                                           screen="inward"))
            when = packed + dt.timedelta(hours=random.randint(5, 7))
            _b, after, m = audit_svc.match("POST", f"/api/outward/{o.id}/receive")
            out = after(db, m, None, rcv) or {}
            t.add(when, rcv, "POST", f"/api/outward/{o.id}/receive", out.get("summary"),
                  ref=out.get("ref"), warehouse_id=o.to_warehouse_id or o.from_warehouse_id)

    # --- whatever the purchasing / operations seeders left -----------------------
    for po in db.query(models.PurchaseOrder).all():
        who = random.choice(["manager2", "manager3", "admin"])
        t.add(po.created_at, who, "POST", "/api/purchase-orders",
              f"raised purchase order {po.po_no or '#' + str(po.id)}"
              + (f" to {po.supplier_name}" if po.supplier_name else ""),
              ref=po.po_no, warehouse_id=po.warehouse_id)
        if po.status and po.status != "draft":
            when = po.confirmed_at or po.cancelled_at or po.updated_at
            t.add(when, who, "POST", f"/api/purchase-orders/{po.id}/status",
                  f"marked purchase order {po.po_no or '#' + str(po.id)} {po.status}",
                  ref=po.po_no, warehouse_id=po.warehouse_id)
    for d in db.query(models.Document).all():
        who = random.choice(_staff_for(users, d.warehouse_id, whs, screen="documents"))
        t.add(d.uploaded_at, who, "POST", "/api/documents/upload",
              "uploaded a supplier invoice to be read", warehouse_id=d.warehouse_id)
        if d.status in ("confirmed", "posted"):
            t.described((d.uploaded_at or NOW) + dt.timedelta(minutes=random.randint(15, 90)),
                        who, "POST", f"/api/documents/{d.id}/confirm")
    for e in db.query(models.LREntry).all():
        who = random.choice(_staff_for(users, e.warehouse_id, whs, admin_ok=False, screen="lr"))
        t.add(e.created_at, who, "POST", "/api/lr",
              f"entered LR {e.lr_no or '—'} ({e.lr_entry_no or '#' + str(e.id)})"
              + (f" from {e.supplier_name}" if e.supplier_name else ""),
              ref=e.lr_entry_no or e.lr_no, warehouse_id=e.warehouse_id)
        if e.received_by and e.created_at:
            t.described(e.created_at + dt.timedelta(hours=random.randint(1, 5)), who,
                        "POST", f"/api/lr/{e.id}/receive")
    for p in db.query(models.Payment).all():
        supplier = p.supplier.name if getattr(p, "supplier", None) else "a supplier"
        t.add(p.created_at, random.choice(["admin", "manager2"]), "POST", "/api/payments",
              f"paid {inr(p.paid_amount)} to {supplier} by {p.mode or 'payment'}"
              f" — receipt {p.receipt_no}", ref=p.receipt_no)
    for r in db.query(models.PurchaseReturn).all():
        wid = r.purchase.warehouse_id if r.purchase else None
        who = random.choice(["admin", "manager2", "manager3"])
        t.add(r.created_at, who, "POST", f"/api/returns/from-purchase/{r.purchase_id}",
              "raised a debit note against GRN "
              f"{(r.purchase.grn_no if r.purchase else None) or '#' + str(r.purchase_id)} (draft)",
              ref=r.purchase.grn_no if r.purchase else None, warehouse_id=wid)
        if r.status == "posted":
            t.described(r.posted_at, who, "POST", f"/api/returns/{r.id}/post")
    for rv in db.query(models.PriceRevision).all():
        field = audit_svc._FIELD_WORD.get(rv.field, rv.field or "price")
        who = rv.created_by if rv.created_by in users else "admin"
        t.add(rv.created_at, who, "POST", "/api/pricing/apply",
              f"changed {field} of {rv.product_count} product(s) — {rv.number}", ref=rv.number)
    for a in db.query(models.PhysicalAudit).filter(models.PhysicalAudit.applied_at.isnot(None)).all():
        t.described(a.applied_at, a.applied_by if a.applied_by in users else "manager2",
                    "POST", f"/api/physical-audit/{a.id}/apply")

    # --- account management (super admin's lines) --------------------------------
    for username, _pw, role, name, wh_codes, active, age, extra in STAFF:
        u = users.get(username)
        if not u:
            continue
        actor = "superadmin" if role != "superboss" else None
        if actor:
            t.add(u.created_at, actor, "POST", "/api/users",
                  f"created account {username} as "
                  f"{users_svc.ROLE_LABEL['user' if username == 'manager2' else role]}",
                  ref=username)
            parts = []
            if (u.permissions or {}).get("screens"):
                parts.append(f"{len(u.permissions['screens'])} screen(s)")
            if (u.permissions or {}).get("warehouses"):
                parts.append(f"{len(u.permissions['warehouses'])} warehouse(s)")
            if (u.permissions or {}).get("data"):
                parts.append(f"{len(u.permissions['data'])} figure(s) withheld")
            if parts:
                t.add(u.created_at + dt.timedelta(minutes=6), actor, "PUT",
                      f"/api/users/{u.id}/permissions",
                      f"changed what {username} may open — " + ", ".join(parts), ref=username)
    def uid(name):
        return users[name].id if name in users else 0
    t.add(work_time(27), "superadmin", "PATCH", f"/api/users/{uid('user07')}",
          "deactivated user07", ref="user07")
    t.add(work_time(19), "superadmin", "POST", f"/api/users/{uid('user03')}/password",
          "reset user03's password — they were signed out everywhere",
          ref="user03", action="password")
    t.add(work_time(12), "superadmin", "PATCH", f"/api/users/{uid('user06')}",
          "deactivated user06", ref="user06")
    t.add(work_time(8), "superadmin", "PATCH", f"/api/users/{uid('user04')}",
          "renamed user04 to “Sample User 04”", ref="user04")
    t.add(work_time(3), "owner", "PATCH", f"/api/users/{uid('manager2')}",
          "changed manager2's role from User to Admin", ref="manager2")

    # --- setup work: the generic sentences the app writes for these screens ------
    stores = db.query(models.Store).order_by(models.Store.id).all()
    tills = db.query(models.PosTerminal).order_by(models.PosTerminal.id).all()
    sups = db.query(models.Supplier).order_by(models.Supplier.id).all()
    cats = {c.code: c.id for c in db.query(models.Catalogue).all()}
    setup = [
        (29, "admin", "POST", "/api/master-data/tax/records", None),
        (29, "admin", "POST", "/api/master-data/tax/records", None),
        (28, "admin", "POST", "/api/master-data/brand/records", None),
        (26, "manager2", "POST", "/api/masters/agents", None),
        (24, "admin", "POST", "/api/master-data/employee/records", None),
        (22, "manager2", "POST", "/api/masters/transports", None),
        (20, "manager3", "POST", f"/api/catalogues/{cats.get('SILKS', 2)}/attributes/color/options", None),
        (17, "admin", "PUT", f"/api/suppliers/{sups[2].id if len(sups) > 2 else 1}", None),
        (15, "manager2", "POST", "/api/masters/options", None),
        (13, "manager3", "POST", f"/api/catalogues/{cats.get('SILKS', 2)}/attributes/weave/options", None),
        (11, "admin", "PUT", "/api/master-data/item/records/1", None),
        (9, "admin", "POST", "/api/suppliers", None),
        (6, "manager2", "POST", "/api/master-data/trade_agreement/records", None),
        (4, "admin", "PUT", f"/api/suppliers/{sups[0].id if sups else 1}", None),
        (2, "admin", "POST", "/api/master-data/employee_in_out/records", None),
        (1, "manager3", "PUT", "/api/master-data/brand/records/1", None),
    ]
    for days, who, method, path, _ in setup:
        _need, scr, act = required_access(method, path)
        t.add(work_time(days), who, method, path, audit_svc.generic(scr, act, path))
    if len(stores) > 3:
        s = stores[3]
        t.add(work_time(21), "admin", "PATCH", f"/api/locations/stores/{s.id}",
              f"changed store {s.name}")
    if tills:
        k = tills[-1]
        t.add(work_time(16), "admin", "PATCH", f"/api/locations/terminals/{k.id}",
              f"changed POS counter {k.name}")
    if len(stores) > 1:
        s = stores[1]
        t.add(work_time(7), "manager2", "PATCH", f"/api/locations/stores/{s.id}",
              f"changed store {s.name}")

    # --- refused attempts and failed sign-ins: what the Command Center flags ------
    some_grn = db.query(models.Purchase).filter(models.Purchase.status == "posted",
                                                models.Purchase.warehouse_id == wh_ids.get("WH-B")).first()
    if some_grn:
        t.add(work_time(10), "user03", "POST", f"/api/returns/from-purchase/{some_grn.id}",
              "was refused: added a debit note — needs admin", outcome="refused",
              warehouse_id=some_grn.warehouse_id)
    prod = db.query(models.Product).order_by(models.Product.id).first()
    if prod:
        t.add(local_at(0, 10, 12), "user05", "PATCH", f"/api/inventory/products/{prod.id}",
              "was refused: changed stock — no Modify access to Inventory", outcome="refused",
              warehouse_id=wh_ids.get("WH-A"))
    t.add(work_time(5), "user02", "POST", "/api/payments",
          "was refused: added a supplier payment — needs admin", outcome="refused",
          warehouse_id=wh_ids.get("WH-A"))
    t.signin(work_time(14), "user07", outcome="refused")
    t.signin(work_time(9), "user04", outcome="failed")
    t.signin(work_time(4), "user2", outcome="failed")          # a typed name, no such account
    t.signin(local_at(0, 9, 3), "user03", outcome="failed")

    # --- sign-ins: who was in, and when (last_login_at follows from these) --------
    rhythm = {"superadmin": 6, "admin": 4, "user": 6, "manager2": 5, "manager3": 7,
              "user02": 7, "user03": 7, "user04": 9, "user05": 7,
              "owner": 12, "user06": None, "user07": None}
    last = {}
    for name, every in rhythm.items():
        if name not in users or not every:
            continue
        day = random.randint(0, every - 1)
        while day <= 29:
            when = local_at(day, random.randint(9, 11), random.randint(0, 59))
            t.signin(when, name)
            last[name] = max(last.get(name, when), when)
            day += every + random.choice([0, 0, 1])
    # the two who left signed in before they left
    for name, days in (("user07", 28), ("user06", 13)):
        if name in users:
            when = local_at(days, 9, random.randint(10, 50))
            t.signin(when, name)
            last[name] = when
    for name, when in last.items():
        users[name].last_login_at = when
    db.commit()
    return t.write()


# ===========================================================================
#  3 · Notifications
# ===========================================================================
RECIPIENTS = [
    ("Sample Owner", "+91 90000 20001", "Owner — everything critical", ["critical"]),
    ("Sample Manager 1", "+91 90000 20002", "Accounts — supplier bills", ["critical", "warn"]),
    ("Sample Manager 2", "+91 90000 20003", "Operations — GRNs, transfers, LRs", ["critical", "warn", "info"]),
    ("Sample Manager 3", "+91 90000 20004", "Silk stock — Sample Warehouse C", ["critical", "warn"]),
]
#: how long each queue has been open, in days — "waiting 6 days" on the bell
WAITING = {"payments.overdue": 9, "deadstock.critical": 21, "deadstock.dead": 14,
           "documents.review": 1, "grn.draft": 6, "grn.shortage": 4, "lr.pending": 2,
           "outward.transit": 2, "deadstock.approaching": 11, "lr.unlinked": 3,
           "outward.draft": 1, "returns.draft": 5, "inventory.detail": 8}


def _notifications(db):
    have = {r.name for r in db.query(models.NotificationRecipient).all()}
    for name, mobile, role, levels in RECIPIENTS:
        if name not in have:
            db.add(models.NotificationRecipient(name=name, mobile=notif_svc.clean_mobile(mobile),
                                                role=role, levels=levels, active=True))
    db.commit()
    notices = notif_svc.collect(db)             # creates a state row per open queue
    for n in notices:
        st = db.query(models.NotificationState).filter(
            models.NotificationState.key == n["key"]).first()
        if not st:
            continue
        st.first_seen = NOW - dt.timedelta(days=WAITING.get(n["key"], 2),
                                           hours=random.randint(1, 6))
        # the info-level detailing queue was looked at and left for later
        if n["key"] == "inventory.detail":
            st.read_level = float(n.get("count") or 0)
            st.read_at = NOW - dt.timedelta(days=1, hours=3)
            st.read_by = "Sample Manager 2"
    db.commit()
    return len(notices), len(RECIPIENTS)


# ===========================================================================
#  4 · Masters
# ===========================================================================
TAXES = [("GST00", "GST 0%", "GST 0%", 0), ("GST05", "GST 5%", "GST 5%", 5),
         ("GST12", "GST 12%", "GST 12%", 12), ("GST18", "GST 18%", "GST 18%", 18),
         ("GST28", "GST 28%", "GST 28%", 28)]
BRANDS = [  # (code, name, type, margin min, max)
    ("BR-A", "Sample Brand A", "Third Party", 30, 40), ("BR-B", "Sample Brand B", "Third Party", 32, 45),
    ("BR-C", "Sample Brand C", "Third Party", 30, 42), ("BR-D", "Sample Brand D", "Third Party", 22, 30),
    ("BR-E", "Sample Brand E", "Third Party", 22, 32), ("BR-F", "Sample Brand F", "Third Party", 30, 40),
    ("BR-G", "Sample Brand G", "Third Party", 30, 38), ("BR-H", "Sample Brand H", "Private Label", 35, 50),
    ("BR-I", "Sample Brand I", "Third Party", 28, 38), ("BR-J", "Sample Brand J", "Third Party", 32, 45),
    ("BR-K", "Sample Brand K", "Third Party", 30, 42), ("BR-L", "Sample Brand L", "Third Party", 25, 35),
    ("BR-M", "Sample Brand M", "Own", 45, 60),
]
SIZE_GROUPS = [  # (code, name, attribute, values)
    ("SG-MSH", "Mens Shirt Sizes", "Size", ["38", "40", "42", "44", "46"]),
    ("SG-ALP", "Alpha Sizes", "Size", ["S", "M", "L", "XL", "2XL"]),
    ("SG-WST", "Waist Sizes", "Size", ["26", "28", "30", "32", "34", "36"]),
    ("SG-KID", "Kids Age Sizes", "Size", ["2-3Y", "4-5Y", "6-7Y", "8-9Y"]),
    ("SG-FRE", "Free Size", "Size", ["Free"]),
    ("CF-CORE", "Core Colours", "Colour", ["White", "Black", "Navy", "Maroon", "Grey Melange"]),
]
PRODUCT_GROUPS = [  # (code, name, type, hsn, tax, gender, size group, holding days)
    ("PG-MSH", "MENS SHIRTS", "Textile", "6205", "GST 5%", "Mens", "Mens Shirt Sizes", 120),
    ("PG-MTS", "MENS T-SHIRTS", "Textile", "6109", "GST 5%", "Mens", "Alpha Sizes", 90),
    ("PG-MBT", "MENS BOTTOMS", "Textile", "6203", "GST 12%", "Mens", "Waist Sizes", 120),
    ("PG-LKU", "LADIES KURTIS & SETS", "Textile", "6204", "GST 12%", "Ladies", "Alpha Sizes", 90),
    ("PG-SSR", "SILK SAREES", "Textile", "5007", "GST 5%", "Ladies", "Free Size", 365),
    ("PG-CSR", "COTTON & PRINTED SAREES", "Textile", "5208", "GST 5%", "Ladies", "Free Size", 180),
    ("PG-KID", "KIDS WEAR", "Textile", "6209", "GST 5%", "Kids", "Kids Age Sizes", 90),
    ("PG-ETH", "ETHNIC SETS", "Textile", "6203", "GST 12%", "Unisex", "Alpha Sizes", 150),
    ("PG-WIN", "WINTER WEAR", "Textile", "6110", "GST 12%", "Unisex", "Alpha Sizes", 200),
    ("PG-ACC", "ACCESSORIES", "Accessory", "4203", "GST 18%", "Unisex", "Free Size", 180),
]
AGENTS = [  # (name, type, contact, phone, city, commission %)
    ("Sample Agent 1", "Buying", "Sample Contact 31", "+91 90000 30001", "Sample City", 2.0),
    ("Sample Agent 2", "Commission", "Sample Contact 32", "+91 90000 30002", "XYZ Town", 1.5),
    ("Sample Agent 3", "Buying", "Sample Contact 33", "+91 90000 30003", "XYZ Town", 2.0),
    ("Sample Agent 4", "Commission", "Sample Contact 34", "+91 90000 30004", "LMN Nagar", 3.0),
    ("Sample Agent 5", "Selling", "Sample Contact 35", "+91 90000 30005", "Sample City", 1.0),
    ("Sample Agent 6", "Buying", "Sample Contact 36", "+91 90000 30006", "LMN Nagar", 2.5),
]
TRANSPORTS = [  # (name, mode, contact, phone, city, price mode, payment)
    ("Sample Transport 1", "Transport", "Sample Contact 41", "+91 90000 40001", "Sample City", "Per Bundle", "TOPAY"),
    ("Sample Transport 2", "Transport", "Sample Contact 42", "+91 90000 40002", "XYZ Town", "Per Box", "Both"),
    ("Sample Transport 3", "Transport", "Sample Contact 43", "+91 90000 40003", "XYZ Town", "Per KG", "PAID"),
    ("Sample Transport 4", "Transport", "Sample Contact 44", "+91 90000 40004", "LMN Nagar", "Per KG", "TOPAY"),
    ("Sample Transport 5", "Courier", "Sample Contact 45", "+91 90000 40005", "Sample City", "Per Box", "PAID"),
    ("Sample Transport 6", "Courier", "Sample Contact 46", "+91 90000 40006", "LMN Nagar", "Fixed", "PAID"),
]
# (code, title, name, gender, dept, designation, location, manager code, joined, gross, active)
EMPLOYEES = [
    ("EMP001", "Mr", "Sample Staff 01", "Male", "Administration", "Director", "NONE", None, "2019-04-01", 0, True),
    ("EMP002", "Ms", "Sample Staff 02", "Female", "Accounts", "Accounts Manager", "NONE", "EMP001", "2020-06-15", 62000, True),
    ("EMP003", "Mr", "Sample Staff 03", "Male", "Operations", "Operations Manager", "NONE", "EMP001", "2020-08-01", 68000, True),
    ("EMP004", "Mr", "Sample Staff 04", "Male", "Warehouse", "Warehouse Manager", "NONE", "EMP003", "2021-01-11", 42000, True),
    ("EMP005", "Ms", "Sample Staff 05", "Female", "Warehouse", "Warehouse Manager", "NONE", "EMP003", "2021-07-05", 40000, True),
    ("EMP006", "Mr", "Sample Staff 06", "Male", "Warehouse", "Depot Manager", "NONE", "EMP003", "2022-02-14", 41000, True),
    ("EMP007", "Mrs", "Sample Staff 07", "Female", "Merchandising", "Silk Merchandiser", "NONE", "EMP003", "2021-11-20", 52000, True),
    ("EMP008", "Ms", "Sample Staff 08", "Female", "Warehouse", "Receiving Clerk", "NONE", "EMP004", "2023-03-01", 21000, True),
    ("EMP009", "Mr", "Sample Staff 09", "Male", "Warehouse", "Packer", "NONE", "EMP004", "2022-09-12", 17500, True),
    ("EMP010", "Ms", "Sample Staff 10", "Female", "Warehouse", "Stock Controller", "NONE", "EMP005", "2023-06-19", 23000, True),
    ("EMP011", "Mr", "Sample Staff 11", "Male", "Warehouse", "Receiving Clerk", "NONE", "EMP004", "2026-08-04", 19000, True),
    ("EMP012", "Mr", "Sample Staff 12", "Male", "Warehouse", "Dispatch Supervisor", "NONE", "EMP005", "2022-12-01", 26000, True),
    ("EMP013", "Mrs", "Sample Staff 13", "Female", "Retail", "Store Manager", "Sample Store 1", "EMP003", "2021-05-10", 36000, True),
    ("EMP014", "Ms", "Sample Staff 14", "Female", "Retail", "Billing Executive", "Sample Store 2", "EMP013", "2024-01-08", 18000, True),
    ("EMP015", "Mr", "Sample Staff 15", "Male", "Retail", "Store Manager", "Sample Store 3", "EMP003", "2022-04-18", 35000, True),
    ("EMP016", "Mr", "Sample Staff 16", "Male", "Warehouse", "Receiving Clerk", "NONE", "EMP004", "2022-10-03", 20000, False),
]
PURCHASE_MANAGERS = [f"Sample Purchaser {i}" for i in range(1, 6)]
EXTRA_OPTIONS = {
    "department": ["Administration", "Accounts", "Operations", "Warehouse", "Merchandising", "Retail"],
    "designation": ["Director", "Accounts Manager", "Operations Manager", "Warehouse Manager",
                    "Depot Manager", "Silk Merchandiser", "Receiving Clerk", "Stock Controller",
                    "Packer", "Dispatch Supervisor", "Store Manager", "Billing Executive"],
    "state": ["Tamil Nadu", "Kerala", "Karnataka", "Maharashtra", "Gujarat"],
    "supplier_group": ["Group A — Local", "Group B — Silk", "Group C — Interstate",
                       "Group D — Brands"],
    "floor": ["Ground Floor", "First Floor", "Silk Hall", "Bridal Floor"],
}
UNIT_RULES = [("kurta set", "SET"), ("dhoti set", "SET"), ("nightwear set", "SET"),
              ("gift set", "SET"), ("combo pack", "SET")]
ON = {"Mand": True, "Show": True, "ROL": False}
SHOW = {"Mand": False, "Show": True, "ROL": False}
OFF = {"Mand": False, "Show": False, "ROL": False}


class Masters:
    def __init__(self, db):
        self.db, self.made = db, {}

    def add(self, key, data, code=None, grids=None, matrix=None, title=None, when=None):
        R = models.MasterRecord
        name = str(title or data.get("name") or code or "").strip()
        q = self.db.query(R).filter(R.master == key)
        q = q.filter(R.code == code) if code else q.filter(R.name == name)
        if q.first():
            return
        when = when or (NOW - dt.timedelta(days=random.randint(25, 160), hours=random.randint(0, 9)))
        data = {**data}
        if code and "code" not in data and key not in ("employee",):
            data["code"] = code
        self.db.add(R(master=key, code=code, name=name, data=data, grids=grids or {},
                      matrix=matrix or {}, active=bool(data.get("active", True)),
                      created_at=when, updated_at=when + dt.timedelta(days=random.randint(0, 20))))
        self.made[key] = self.made.get(key, 0) + 1


def _masters(db, whs):
    masters_svc.seed_options(db)        # the fixed lists, as the server seeds them on start
    ut_svc.seed(db)
    from app.services import locations as loc_svc
    loc_svc.mirror_to_options(db)       # active stores into "Transfer Locations"

    # ---- the "in use by this app" lists ----
    for name in PURCHASE_MANAGERS:
        masters_svc.get_or_create_option(db, "purchase_manager", name)
    for kind, values in EXTRA_OPTIONS.items():
        have = {o.value for o in db.query(models.MasterOption).filter(models.MasterOption.kind == kind)}
        for i, v in enumerate(values):
            if v not in have:
                db.add(models.MasterOption(kind=kind, value=v, sort=i))
    for name, *_rest in AGENTS:
        a = masters_svc.get_or_create_agent(db, name)
        a.phone = a.phone or _rest[2]
    for name, *_rest in TRANSPORTS:
        tr = masters_svc.get_or_create_transport(db, name)
        tr.phone = tr.phone or _rest[2]
    for pattern, code in UNIT_RULES:
        if not db.query(models.UnitRule).filter(models.UnitRule.pattern == pattern).first() \
                and ut_svc.get(db, code):
            db.add(models.UnitRule(pattern=pattern, scope="keyword", unit_type=code,
                                   source="human", hits=random.randint(3, 40)))
    db.commit()

    m = Masters(db)
    biz = db.query(models.Business).order_by(models.Business.id).first()
    store_names = [s.name for s in db.query(models.Store).filter(models.Store.active.is_(True))
                   .order_by(models.Store.id)]

    # ---- tax, size groups, product groups ----
    for code, name, charge, rate in TAXES:
        m.add("tax", {"name": name, "tax_charges": charge, "rate": rate,
                      "sales_tax": True, "purchase_tax": True, "disable": False}, code=code)
    for i, (code, name, attr, values) in enumerate(SIZE_GROUPS):
        m.add("attribute_filter", {"name": name, "attribute": attr, "values": values,
                                   "sort_order": i + 1, "active": True}, code=code)
    attrs_for = {"Textile": ["BRAND", "MATERIAL", "PATTERN", "COLOUR", "SIZE", "DESIGN", "FIT", "UPLOAD QR"],
                 "Accessory": ["BRAND", "COLOUR", "SIZE", "UPLOAD QR"]}
    for code, name, typ, hsn, tax, gender, sizes, hold in PRODUCT_GROUPS:
        wanted = attrs_for[typ]
        matrix = {row: (ON if row in wanted[:5] else SHOW if row in wanted else OFF)
                  for row in models_matrix_rows()}
        for row in ("COLOUR", "SIZE"):
            matrix[row] = {"Mand": True, "Show": True, "ROL": True}
        m.add("product", {
            "name": name, "type": typ, "hsn": hsn, "sales_tax": tax, "purchase_tax": tax,
            "margin_min": random.choice([25, 28, 30]), "margin_max": random.choice([40, 45, 55]),
            "discount_mode": "Allow Discount On Item", "discount_value": 0,
            "selling_mode": "Pack", "qr_label": "Product (SKU) + per piece", "qr_size_mm": 32,
            "serialise": True, "match_supplier_code": True, "uom": "PCS", "size_group": sizes,
            "stock_holding_days": hold, "purchase_plan_mode": "Seasonal" if "SILK" in name else "Manual",
            "expected_gender": gender, "is_core": name in ("MENS SHIRTS", "SILK SAREES"),
            "active": True}, code=code, matrix=matrix)

    # ---- brands ----
    groups = [g[1] for g in PRODUCT_GROUPS]
    for code, name, typ, lo, hi in BRANDS:
        m.add("brand", {"name": name, "printing_name": name.upper(), "brand_type": typ,
                        "margin_min": lo, "margin_max": hi, "discount_mode": "Percentage",
                        "discount_value": random.choice([0, 0, 5, 10]), "active": True},
              code=code, grids={"b2b_margin": [{"product": g, "margin": lo - 5}
                                               for g in random.sample(groups, 2)]})
    # any brand on stock that the list above does not name
    known = {b[1].lower() for b in BRANDS}
    for (bname,) in db.query(models.Product.brand).distinct():
        if bname and bname.lower() not in known:
            known.add(bname.lower())
            m.add("brand", {"name": bname, "printing_name": bname.upper(),
                            "brand_type": "Unspecified", "active": True},
                  code="BR-" + "".join(w[0] for w in bname.split())[:4].upper()
                       + str(random.randint(10, 99)))

    # ---- items: real SKUs, one per product line ----
    def group_of(p):
        c = (p.category or "").upper()
        silk = "SILK" in (p.material or "").upper()
        for words, g in ((("SAREE",), "SILK SAREES" if silk else "COTTON & PRINTED SAREES"),
                         (("KIDS", "PAVADAI"), "KIDS WEAR"), (("JERKIN",), "WINTER WEAR"),
                         (("BELT",), "ACCESSORIES"), (("KURTA", "DHOTI"), "ETHNIC SETS"),
                         (("MENS-T-SHIRT",), "MENS T-SHIRTS"), (("MENS-SHIRT",), "MENS SHIRTS"),
                         (("MENS-PANT",), "MENS BOTTOMS"), (("LADIES",), "LADIES KURTIS & SETS")):
            if any(w in c for w in words):
                return g
        return "MENS SHIRTS"
    seen = set()
    for p in db.query(models.Product).order_by(models.Product.id).all():
        base = " ".join((p.description or "").split()[:-1]) or p.description
        if base in seen or not p.sku:
            continue
        seen.add(base)
        m.add("item", {
            "product": group_of(p), "item_code": p.sku, "design": p.design_no or "",
            "selling_name": p.description, "printing_name": (p.description or "")[:24],
            "brand_name": p.brand or "Sample Brand M", "size": p.size or "", "color": p.color or "",
            "pattern": p.pattern or "", "material": p.material or "", "fit": p.fit or "",
            "reorder_min": 6, "reorder_max": 36, "stock_age": 90,
            "pur_rate": round(float(p.avg_cost or 0), 2), "sale_rate": float(p.sale_price or 0),
            "mrp": float(p.mrp or 0), "show_in_list": True, "active": True},
            code=p.sku, title=p.description)

    # ---- suppliers (their ERP fields, against the Supplier rows on file) ----
    contacts = [f"Sample Contact {n}" for n in range(11, 23)]
    for i, s in enumerate(db.query(models.Supplier).order_by(models.Supplier.id).all()):
        bank = [x.strip() for x in str(s.bank or "").split("·")] if isinstance(s.bank, str) else []
        city = (s.address or "").split(",")[-1].strip() or ""
        m.add("supplier", {
            "gstin": s.gstin or "", "name": s.name, "company_reg_name": f"{s.name} Pvt Ltd",
            "contact_person": contacts[i % len(contacts)], "contact_no": s.phone or "+91 90000 00000",
            "address": s.address or city or "—", "state": s.state or "", "email": s.email or "",
            "transport": TRANSPORTS[i % len(TRANSPORTS)][0],
            "supplier_group": ("Group B — Silk" if i in (3, 4)
                               else "Group A — Local" if (s.state_code or "") == "33" else "Group C — Interstate"),
            "payment_credit_days": random.choice([30, 45, 60]), "cash_discount_pct": random.choice([0, 1, 2]),
            "cash_discount_days": 7, "rating": random.choice([3, 4, 4, 5]), "taxable": True,
            "support_po": True, "tds_group": "194Q", "interstate_sale": (s.state_code or "") != "33",
            "agent_name": AGENTS[i % len(AGENTS)][0] if i % 3 else "",
            "tan_pan": s.pan or "", "bank": bank[0] if bank else "",
            "account_no": bank[1] if len(bank) > 1 else "", "ifsc": bank[2] if len(bank) > 2 else "",
            "bank_account_name": s.name, "active": True}, code=f"SUP{i + 1:03d}")
    sup_names = [s.name for s in db.query(models.Supplier).order_by(models.Supplier.id).limit(6)]

    # ---- agents & transporters (their ERP fields) ----
    for i, (name, typ, contact, phone, city, pct) in enumerate(AGENTS):
        m.add("agent", {"agent_type": typ, "name": name, "contact_person": contact,
                        "contact_no": phone, "city": city, "state": "Tamil Nadu",
                        "commission_pct": pct, "tax": "GST 18%",
                        "email": f"agent{i + 1}@sample.example", "active": True},
              code=f"AGT{i + 1:03d}")
    for i, (name, mode, contact, phone, city, price, pay) in enumerate(TRANSPORTS):
        m.add("transport", {"business_mode": mode, "name": name, "contact_person": contact,
                            "contact_no": phone, "city": city,
                            "state": "Tamil Nadu", "email": f"transport{i + 1}@sample.example",
                            "bank_account_name": name, "bank": "Sample Bank",
                            "price_mode": price, "payment_mode": pay, "tax": "GST 5%",
                            "vehicles": f"TN 00 AA {1001 + i}",
                            "loading_per_box": random.choice([20, 25, 30]),
                            "loading_per_bundle": random.choice([35, 40, 50]), "active": True},
              code=f"TRN{i + 1:03d}",
              grids={"city_rates": [{"city": c, "per_kg": random.choice([6, 7, 8, 9]),
                                     "per_box": random.choice([90, 110, 140]),
                                     "per_bundle": random.choice([150, 180, 220])}
                                    for c in ("Sample City", "XYZ Town", "LMN Nagar", "PQR Village")[:2 + i % 3]]})

    # ---- trade agreements, tailors ----
    for i, sup in enumerate(sup_names[:3]):
        m.add("trade_agreement", {
            "name": f"{sup} — FY 2026-27 terms", "party_type": "Supplier", "supplier": sup,
            "valid_from": "2026-04-01", "valid_to": "2027-03-31",
            "discount_pct": [2, 3, 1.5][i], "margin_pct": [30, 32, 28][i], "credit_days": [45, 60, 30][i],
            "remarks": "Rates held for the financial year; freight to pay by the supplier above ₹50,000.",
            "active": True}, code=f"TA-2026-{i + 1:02d}",
            grids={"terms": [{"product": groups[i], "brand": BRANDS[i + 1][1],
                              "rate": [420, 165, 310][i], "discount_pct": 2}]})
    for name, phone, email, works in [
            ("Sample Tailor 1", "+91 90000 50001", "tailor1@sample.example",
             [("Blouse stitching", 450), ("Fall & pico", 120), ("Saree tassels", 180)]),
            ("Sample Tailor 2", "+91 90000 50002", "tailor2@sample.example",
             [("Pant hemming", 80), ("Shirt alteration", 120), ("Kurti fitting", 150)]),
            ("Sample Tailor 3", "+91 90000 50003", "tailor3@sample.example",
             [("Kurta set stitching", 950), ("Jacket alteration", 600)])]:
        m.add("tailor", {"name": name, "contact_no": phone, "email": email, "city": "Sample City",
                         "state": "Tamil Nadu", "bank": "Sample Bank", "bank_account_name": name,
                         "active": True},
              grids={"works": [{"work": w, "charge": c, "delay_per_day": 25, "delay_maximum": 200,
                                "priority": i + 1} for i, (w, c) in enumerate(works)]})

    # ---- configuration (singletons) ----
    sku = db.query(models.Product.sku).filter(models.Product.sku.isnot(None)).first()
    m.add("configuration", {
        "name": "Default", "company_name": biz.legal_name if biz else "Sample Company Pvt Ltd",
        "gstin": biz.gstin if biz else "", "address": biz.address if biz else "",
        "financial_year_from": "2026-04-01", "financial_year_to": "2027-03-31",
        "default_uom": "PCS", "round_off": True, "allow_negative_stock": False,
        "sku_prefix": (sku[0].split("-")[0] + "-") if sku and "-" in sku[0] else "ESSA-",
        "qr_size_mm": 32, "bundle_qr_size_mm": 40, "grn_prefix": "GRN-",
        "stock_holding_days": 90, "active": True}, title="Default")
    m.add("hr_configuration", {
        "name": "Default", "shift_start": "09:30", "shift_end": "19:30", "grace_minutes": 15,
        "half_day_hours": 4, "full_day_hours": 8, "ot_after_hours": 9, "week_off_day": "Sunday",
        "casual_leave": 12, "sick_leave": 6, "earned_leave": 15, "pf_applicable": True,
        "pf_pct": 12, "esi_applicable": True, "esi_pct": 0.75, "active": True}, title="Default")

    # ---- salary structures, employees, incharges, attendance ----
    for code, name, mode, basic, hra, comps in [
            ("SAL-MGR", "Managers — Monthly", "Monthly", 50, 20,
             [("Basic", "Earning", "% of Gross", 50), ("HRA", "Earning", "% of Basic", 40),
              ("PF", "Deduction", "% of Basic", 12), ("Professional Tax", "Deduction", "Fixed", 208)]),
            ("SAL-STF", "Warehouse & Store Staff", "Monthly", 55, 15,
             [("Basic", "Earning", "% of Gross", 55), ("HRA", "Earning", "% of Basic", 30),
              ("Attendance Bonus", "Earning", "Fixed", 750), ("ESI", "Deduction", "% of Gross", 0.75)]),
            ("SAL-DLY", "Daily Wage — Packers", "Daily", 100, 0,
             [("Daily Wage", "Earning", "Fixed", 650), ("OT per hour", "Earning", "Fixed", 90)])]:
        m.add("salary_management", {"name": name, "salary_mode": mode, "basic_pct": basic,
                                    "hra_pct": hra, "da_pct": 0, "conveyance": 1600 if mode == "Monthly" else 0,
                                    "other_allowance": 0, "active": True}, code=code,
              grids={"components": [{"component": c, "kind": k, "calc": cl, "value": v}
                                    for c, k, cl, v in comps]})
    by_code = {e[0]: e for e in EMPLOYEES}

    def full(code):
        e = by_code.get(code)
        return e[2] if e else ""
    login = {"EMP001": "owner", "EMP002": "admin", "EMP003": "manager2", "EMP007": "manager3",
             "EMP008": "user02", "EMP009": "user", "EMP010": "user03", "EMP011": "user05",
             "EMP012": "user04", "EMP016": "user07"}
    wh_of = {"EMP005": "XYZ Town", "EMP010": "XYZ Town", "EMP012": "XYZ Town",
             "EMP006": "LMN Nagar", "EMP007": "LMN Nagar"}
    pins = {"Sample City": "600010", "XYZ Town": "600020", "LMN Nagar": "600030"}
    for code, title, name, gender, dept, desig, loc, mgr, joined, gross, active in EMPLOYEES:
        city = wh_of.get(code, "Sample City")
        m.add("employee", {
            "employee_code": code, "title": title, "name": name, "surname": "",
            "gender": gender, "contact_no": f"+91 90000 6{code[-3:]}0",
            "email": f"staff{code[-2:]}@sample.example",
            "date_of_birth": f"19{random.randint(78, 99)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
            "date_of_joining": joined, "department": dept, "designation": desig,
            "working_location": loc if loc in store_names or loc == "NONE" else "NONE",
            "manager": full(mgr) if mgr else "", "working_mode": "Full Time",
            "working_hour": "9 Hours", "allow_system": code in login, "username": login.get(code, ""),
            "salary_mode": "Daily" if desig == "Packer" else "Monthly",
            "salary_structure": ("Managers — Monthly" if "Manager" in desig or desig in ("Director", "Silk Merchandiser")
                                 else "Daily Wage — Packers" if desig == "Packer" else "Warehouse & Store Staff"),
            "gross_pay": gross, "week_off": "Sunday", "leave_encashment": "On Exit",
            "pa_street": "ABC Area", "pa_city": city, "pa_district": city, "pa_state": "Tamil Nadu",
            "pa_pincode": pins[city], "active": active}, code=code, title=name,
            when=dt.datetime.combine(dt.date.fromisoformat(joined), dt.time(5, 0))
            if joined > "2026-04-01" else None)
    for emp, loc, typ in [("EMP013", 0, "Floor"), ("EMP015", 2, "Counter"),
                          ("EMP014", 1, "Counter"), ("EMP007", 4, "Section")]:
        if loc < len(store_names):
            m.add("employee_incharge", {"employee": full(emp), "location": store_names[loc],
                                        "type": typ, "active": True}, title=full(emp),
                  grids={"incentives": [{"location": store_names[loc], "type": typ,
                                         "select": ["Ground Floor", "Bridal Floor", "Silk Hall"][loc % 3],
                                         "incentive_mode": "%", "incentive": random.choice([0.5, 1, 1.5])}]})
    present = [e for e in EMPLOYEES if e[10] and e[0] != "EMP001"][:11]
    for back in range(1, 5):
        day = TODAY - dt.timedelta(days=back)
        if day.weekday() == 6:                  # Sunday is the week off
            continue
        for e in present:
            name = e[2]
            roll = random.random()
            status = "Present" if roll > 0.12 else random.choice(["Half Day", "Leave"])
            late = random.choice([0, 0, 0, 5, 12, 20]) if status == "Present" else 0
            m.add("employee_in_out", {
                "employee": name, "date": day.isoformat(),
                "in_time": "" if status == "Leave" else f"09:{30 + late:02d}" if late < 30 else "10:05",
                "out_time": "" if status == "Leave" else ("14:00" if status == "Half Day"
                                                         else f"19:{random.choice([30, 35, 45]):02d}"),
                "status": status, "late_minutes": late,
                "ot_hours": random.choice([0, 0, 0, 1, 2]) if status == "Present" else 0,
                "location": e[6] if e[6] in store_names else "NONE",
                "remarks": "Stock count overtime" if status == "Present" and random.random() < 0.1 else ""},
                title=f"{name} · {day.isoformat()}",
                when=dt.datetime.combine(day, dt.time(14, 30)))

    # ---- product attributes (which vocabulary each product group uses) ----
    for g, brands, sizes, colours, materials, patterns, fits in [
            ("MENS SHIRTS", ["Sample Brand A", "Sample Brand M"], ["38", "40", "42", "44"],
             ["White", "Sky Blue", "Red Check", "Navy Check"], ["Cotton", "Linen Blend"], ["Solid", "Checked", "Striped"], ["Regular", "Slim"]),
            ("MENS T-SHIRTS", ["Sample Brand B"], ["S", "M", "L", "XL"], ["Black", "Olive", "Maroon", "Grey Melange"],
             ["Cotton Jersey", "Pique Cotton"], ["Solid", "Printed"], ["Regular", "Relaxed"]),
            ("SILK SAREES", ["Sample Brand D", "Sample Brand E"], ["Free"],
             ["Arakku Red", "Peacock Green", "Royal Blue", "Gold", "Rani Pink"], ["Pure Silk", "Soft Silk"], ["Woven"], []),
            ("KIDS WEAR", ["Sample Brand J", "Sample Brand D"], ["2-3Y", "4-5Y", "6-7Y", "8-9Y"],
             ["Yellow", "Peach", "Blue", "Red", "Magenta"], ["Cotton", "Silk"], ["Printed", "Solid"], ["Regular"])]:
        m.add("product_attributes", {"product": g, "brands": brands, "sizes": sizes, "colours": colours,
                                     "materials": materials, "patterns": patterns, "fits": fits,
                                     "active": True}, title=g)
    db.commit()
    return m.made


def models_matrix_rows():
    from app.services import master_defs
    return list(master_defs.PURCHASE_ENTRY_ATTRS)


# ===========================================================================
#  5 · Catalogues
# ===========================================================================
SILK_OPTIONS = {
    "color": ["Arakku Red", "Peacock Green", "Royal Blue", "Gold", "Rani Pink", "Mint", "Cream",
              "Magenta", "Mustard Yellow", "Bottle Green", "Maroon", "Off White", "Parrot Green",
              "Mango Yellow", "Lavender", "Navy Blue"],
    "material": ["Pure Silk", "Silk", "Soft Silk", "Silk Cotton", "Tussar Silk", "Organza", "Art Silk"],
    "weave": ["Plain Weave", "Jacquard", "Twill"],
    "zari": ["Silver", "Copper", "No Zari"],
    "border": ["Double Side", "Broad Border", "Thin Border", "Thread Work"],
    "pattern": ["Butta", "Brocade", "Checks", "Stripes", "Temple Motif", "Floral", "Paisley", "Plain Body"],
    "occasion": ["Bridal", "Festive", "Party Wear", "Daily Wear"],
}
GARMENT_OPTIONS = {
    "fit": ["Regular", "Slim", "Relaxed"],
    "pattern": ["Solid", "Printed", "Checked", "Striped", "Woven"],
    "material": ["Cotton Jersey", "Pique Cotton", "Denim", "Wool Blend", "Georgette", "Rayon",
                 "Cotton Silk", "Handloom Cotton", "Leather", "Linen Blend"],
    "color": ["Sky Blue", "Red Check", "Navy Check", "Olive", "Grey Melange", "Mustard", "Teal",
              "Lavender", "Indigo", "Ice Blue", "Charcoal", "Camel", "Wine", "Emerald", "Coral",
              "Peach", "Ivory", "Bottle Green", "Ruby", "Turquoise", "Rust", "Lilac"],
    "size": ["2-3Y", "4-5Y", "6-7Y", "8-9Y", "Free"],
    "brand": [b[1] for b in BRANDS],
}
SILK_CATEGORIES = ["SILK-PURE SILK SAREE", "SILK-ART SILK SAREE", "SILK-SOFT SILK SAREE",
                   "SILK-TUSSAR SAREE", "SILK-BRIDAL SAREE", "SILK-DHOTI SET", "SILK-PATTU PAVADAI",
                   "SILK-BLOUSE PIECE", "SILK-DUPATTA", "SILK-ANGAVASTRAM"]


def _catalogues(db):
    added = 0
    garments = cat_svc.default_catalogue(db)
    silks = db.query(models.Catalogue).filter(models.Catalogue.code == "SILKS").first()
    if silks:
        have = {a.key for a in db.query(models.CatalogueAttribute).filter(
            models.CatalogueAttribute.catalogue_id == silks.id)}
        n = len(have)
        # descriptive, not identity: two sarees differing only in occasion are one item
        for key in ("pattern", "occasion"):
            if key not in have:
                db.add(models.CatalogueAttribute(
                    catalogue_id=silks.id, key=key, label=cat_svc.COLUMN_LABELS.get(key),
                    column=key if key in cat_svc.COLUMN_ATTRS else None,
                    identity=False, sort=n, active=True))
                n += 1
        db.flush()
        for attr, values in SILK_OPTIONS.items():
            for v in values:
                before = db.query(models.AttributeOption).count()
                cat_svc.add_option(db, silks.id, attr, v)
                added += db.query(models.AttributeOption).count() - before
        have_cats = {c.name for c in db.query(models.Category).filter(
            models.Category.catalogue_id == silks.id)}
        for name in SILK_CATEGORIES:
            if name not in have_cats:
                db.add(models.Category(catalogue_id=silks.id, section="SILKS", name=name))
    if garments:
        for attr, values in GARMENT_OPTIONS.items():
            for v in values:
                before = db.query(models.AttributeOption).count()
                cat_svc.add_option(db, garments.id, attr, v)
                added += db.query(models.AttributeOption).count() - before
    # every value already on stock is offered in its own catalogue's dropdown —
    # for the attributes that catalogue actually records
    declared = {}
    for a in db.query(models.CatalogueAttribute).filter(models.CatalogueAttribute.active.is_(True)):
        declared.setdefault(a.catalogue_id, set()).add(a.key)
    for p in db.query(models.Product).all():
        cid = p.catalogue_id or (garments.id if garments else None)
        if not cid:
            continue
        for attr in ("color", "material", "fit", "pattern", "size", "brand"):
            v = getattr(p, attr, None)
            if v and attr in declared.get(cid, ()):
                cat_svc.add_option(db, cid, attr, v)
    db.commit()
    return added


# ===========================================================================
def run(db):
    whs = {w.code: w for w in db.query(models.Warehouse).all() if w.code}
    users = _users(db, whs)
    made = _masters(db, whs)
    options = _catalogues(db)
    events = _audit(db, users, whs)
    notices, recipients = _notifications(db)
    summary = {
        "users": db.query(models.User).count(),
        "active_users": db.query(models.User).filter(models.User.active.is_(True)).count(),
        "audit_events_added": events,
        "audit_events_total": db.query(models.AuditEvent).count(),
        "notices_open": notices, "notification_recipients": recipients,
        "master_records": made,
        "agents": db.query(models.Agent).count(), "transports": db.query(models.Transport).count(),
        "attribute_options_added": options,
    }
    print(summary)
    return summary


if __name__ == "__main__":
    session = SessionLocal()
    try:
        run(session)
    finally:
        session.close()
