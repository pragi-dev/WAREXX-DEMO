"""Purchasing for the static demo: orders, consignments, supplier invoices.

    set ESSA_DATABASE_URL=sqlite:///C:/path/to/demo-build/demo.db
    set ESSA_STATE_DIR=C:/path/to/demo-build
    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\seed_purchasing.py

Runs AFTER seed_base.py, on the same scratch database. It fills the buying side
of the sample company the way the screens would — through the application's
own services wherever one exists:

  * Purchase Orders in every lifecycle state (services/purchase_orders),
  * the LR Entry register: consignments booked against confirmed orders, rows
    read off a transporter's register page, received and still on the road
    (routers/lr's own write funnel, services/lr, services/lr_link),
  * supplier invoices in every Invoice Entry stage — to review, checked,
    receiving, in stock, short — each with a rendered tax-invoice PNG stored
    where the document upload stores files (services/storage), its reading in
    the canonical invoice schema (extraction/base, extraction/validate), and
    GRNs, shortages and a debit note built by services/inventory, shortages
    and returns.

Every name here is a generic placeholder (Sample Supplier 01, Sample Staff 11, ...).

It refuses unless the database is a sqlite file inside a 'demo-build' folder and
the uploads folder (ESSA_STATE_DIR / ESSA_UPLOAD_DIR) is inside one too: it
writes invoice images, and the default upload folder is inside the repository.
"""
import copy
import datetime as dt
import hashlib
import io
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "..", "app", "backend"))
sys.path.insert(0, BACKEND)
sys.path.insert(0, HERE)

URL = os.environ.get("ESSA_DATABASE_URL", "")
if not URL.startswith("sqlite:///") or "demo-build" not in URL.replace("\\", "/"):
    sys.exit("Refusing: ESSA_DATABASE_URL must be a sqlite file inside a 'demo-build' "
             "folder. This script writes demo data into it.")
_UPLOADS = os.environ.get("ESSA_UPLOAD_DIR") or os.environ.get("ESSA_STATE_DIR") or ""
if "demo-build" not in _UPLOADS.replace("\\", "/"):
    sys.exit("Refusing: set ESSA_STATE_DIR (or ESSA_UPLOAD_DIR) to the 'demo-build' "
             "folder — invoice images are written there, and the default is inside "
             "the repository.")

random.seed(11)          # the same story on every build

from PIL import Image, ImageDraw, ImageFont                       # noqa: E402

from app import models                                             # noqa: E402
from app.database import SessionLocal                              # noqa: E402
from app.extraction.base import empty_invoice                      # noqa: E402
from app.extraction.validate import normalise_and_check            # noqa: E402
from app.routers import lr as lr_router                            # noqa: E402
from app.services import businesses                                # noqa: E402
from app.services import inventory as inv                          # noqa: E402
from app.services import lr as lr_svc                              # noqa: E402
from app.services import lr_link                                   # noqa: E402
from app.services import purchase_orders as po_svc                 # noqa: E402
from app.services import returns as ret_svc                        # noqa: E402
from app.services import shortages as short_svc                    # noqa: E402
from app.services import storage                                   # noqa: E402

# seed_base's product list is the catalogue every supplier sells from. Imported
# for its tables only; it re-seeds `random` on import, so ours is set again.
import seed_base as base                                           # noqa: E402
random.seed(11)

TODAY = dt.datetime.utcnow().replace(hour=6, minute=0, second=0, microsecond=0)
GST = 5.0                       # apparel and silk fabric, at these price points

PURCHASERS = ["Sample Purchaser 1", "Sample Purchaser 2", "Sample Purchaser 3"]
RECEIVERS = ["Sample Staff 11", "Sample Staff 12", "Sample Staff 13", "Sample Staff 14"]
TRANSPORTS = ["Sample Transport 1", "Sample Transport 2", "Sample Transport 3",
              "Sample Transport 4", "Sample Transport 5"]
COURIER = "Sample Courier 1"
AGENTS = ["Sample Agent 1", "Sample Agent 2", "Direct"]


def _when(days_ago, hour=5):
    return TODAY - dt.timedelta(days=days_ago) + dt.timedelta(hours=hour - 6)


def _iso(d):
    return d.strftime("%Y-%m-%d")


# ===========================================================================
#  entry point
# ===========================================================================
def run(db):
    if db.query(models.PurchaseOrder).count() or \
            db.query(models.Document).count() or db.query(models.LREntry).count():
        print("seed_purchasing: purchase orders / documents / LR rows already exist - "
              "run it on a fresh copy of the base database. Nothing written.")
        return
    ctx = _context(db)
    _fill_grn_totals(db)
    pos = _purchase_orders(db, ctx)
    lrs = _lr_entries(db, ctx, pos)
    docs = _invoices(db, ctx, pos, lrs)
    db.commit()
    _report(db, docs)


def _context(db):
    biz = businesses.default_business(db)
    whs = {w.code: w for w in db.query(models.Warehouse).all()}
    sups = {s.name: s for s in db.query(models.Supplier).all()}
    missing = [c for c in ("WH-A", "WH-B", "WH-C") if c not in whs]
    if missing or len(sups) < 12:
        sys.exit("seed_purchasing: run seed_base.py first (warehouses/suppliers missing).")
    return {"biz": biz, "whs": whs, "sups": sups}


def _fill_grn_totals(db):
    """seed_base raises its GRNs from lines alone, so their headers read ₹0 on
    Invoice Entry. Give each the taxable / tax / grand total its lines imply."""
    for p in db.query(models.Purchase).all():
        if p.grand_total:
            continue
        taxable = round(sum(float(l.amount or 0) for l in p.lines), 2)
        tax = round(taxable * GST / 100, 2)
        p.taxable_total, p.tax_total = taxable, tax
        p.grand_total = float(round(taxable + tax))
    db.commit()


# ===========================================================================
#  Purchase orders
# ===========================================================================
# (key, supplier, warehouse, days ago, status, discount %, purchaser, agent,
#  transport, notes)
PO_PLAN = [
    ("po01", "Sample Supplier 01", "WH-A", 34, "confirmed", 2.0, 0, 2, 0,
     "Deliver by {due}. 30 days credit from GRN. Shirts folded, 6 pcs per poly pack."),
    ("po02", "Sample Supplier 02", "WH-B", 28, "confirmed", 0.0, 1, 2, 1,
     "Deliver by {due}. Size labels inside neck."),
    ("po03", "Sample Supplier 06", "WH-B", 25, "confirmed", 3.0, 0, 0, 2,
     "Deliver by {due} in two lots. 45 days credit. Rate inclusive of packing."),
    ("po04", "Sample Supplier 03", "WH-A", 40, "confirmed", 0.0, 2, 0, 0,
     "Deliver by {due}. Any shortage to be debited at bill rate."),
    ("po05", "Sample Supplier 04", "WH-C", 12, "confirmed", 0.0, 1, 1, 3,
     "Deliver by {due}. Quality certificate with every piece."),
    ("po06", "Sample Supplier 11", "WH-A", 20, "confirmed", 2.5, 2, 0, 4,
     "Deliver by {due}. Season launch — hangers and tags to be attached."),
    ("po07", "Sample Supplier 05", "WH-C", 6, "pending", 0.0, 1, 1, 4,
     "Awaiting supplier confirmation of grade. Deliver by {due}."),
    ("po08", "Sample Supplier 07", "WH-B", 4, "pending", 1.5, 0, 0, 2,
     "Quote valid 15 days. Deliver by {due}."),
    ("po09", "Sample Supplier 10", "WH-A", 3, "draft", 0.0, 2, 2, 0,
     "Draft — confirm size ratio with Sample Store 1 before sending."),
    ("po10", "Sample Supplier 12", "WH-B", 1, "draft", 0.0, 1, 2, 1,
     "Draft — product tags required. Deliver by {due}."),
    ("po11", "Sample Supplier 08", "WH-A", 0, "draft", 5.0, 0, 2, 3,
     "Capsule range for Sample Store 2. Rates under negotiation."),
    ("po12", "Sample Supplier 07", "WH-A", 50, "cancelled", 0.0, 0, 0, 2,
     "Deliver by {due}."),
    ("po13", "Sample Supplier 10", "WH-B", 45, "cancelled", 0.0, 2, 2, 0,
     "Deliver by {due}."),
]
CANCEL_REASONS = {
    "po12": "Supplier could not commit to the delivery date",
    "po13": "Rates revised by supplier — re-raised as a fresh order",
}


def _supplier_products(name):
    idx = [s[0] for s in base.SUPPLIERS].index(name)
    return [p for p in base.PRODUCTS if p[9] == idx]


def _size_mix(sizes, big):
    """A size run as the buying office writes it: "M:8, L:12, XL:8"."""
    if sizes == ["Free"]:
        q = random.choice([4, 6, 8]) if big else random.choice([12, 18, 24])
        return f"Free:{q}", q, {"Free": q}
    per = {}
    for i, s in enumerate(sizes):
        mid = i in (1, 2) or len(sizes) <= 2
        per[s] = random.choice([8, 10, 12]) if mid else random.choice([4, 6])
    return ", ".join(f"{s}:{q}" for s, q in per.items()), sum(per.values()), per


def _po_lines(name):
    lines = []
    for desc, cat, hsn, rate, mrp, brand, sizes, colours, mat, _ in _supplier_products(name):
        for colour in colours:
            mix, qty, per = _size_mix(sizes, rate >= 2000)
            lines.append(dict(
                particulars=f"{desc} {colour.upper()}", size=mix, qty=qty, uom="PCS",
                rate=rate, brand=brand, design_no=f"{brand[:2].upper()}-{random.randint(1000, 9999)}",
                hsn=hsn, notes=f"{mat} · MRP {mrp}",
                _per=per, _mrp=mrp, _colour=colour, _desc=desc))
    return lines[:6]


def _purchase_orders(db, ctx):
    out = {}
    for key, sup_name, wh_code, days, status, disc, pu, ag, tr, notes in PO_PLAN:
        sup, wh = ctx["sups"][sup_name], ctx["whs"][wh_code]
        when = _when(days, hour=random.randint(4, 9))
        due = (when + dt.timedelta(days=random.choice([14, 18, 21]))).strftime("%d-%b-%Y")
        lines = _po_lines(sup_name)
        item = sorted({l["_desc"].title() for l in lines})
        payload = dict(
            po_date=_iso(when), supplier_id=sup.id, supplier_name=sup.name,
            brand=lines[0]["brand"] if lines else None,
            item=", ".join(item)[:60], place=(sup.address or "").split(",")[-1].strip(),
            transport=TRANSPORTS[tr], agent=AGENTS[ag], purchaser=PURCHASERS[pu],
            discount_pct=disc or None, notes=notes.format(due=due),
            lines=[{k: v for k, v in l.items() if not k.startswith("_")} for l in lines])
        po = po_svc.create(db, payload, warehouse_id=wh.id)
        po.created_at = when
        db.flush()
        if status in ("pending", "confirmed", "cancelled"):
            po_svc.set_status(db, po, "pending")
        if status == "confirmed":
            po_svc.set_status(db, po, "confirmed", by=PURCHASERS[pu])
            po.confirmed_at = when + dt.timedelta(days=1, hours=3)
        if status == "cancelled":
            po_svc.set_status(db, po, "cancelled", reason=CANCEL_REASONS[key])
            po.cancelled_at = when + dt.timedelta(days=4)
        po.updated_at = po.confirmed_at or po.cancelled_at or when
        db.flush()
        out[key] = {"po": po, "lines": lines, "sup": sup, "wh": wh, "when": when}
    db.commit()
    return out


# ===========================================================================
#  Invoice data (canonical schema) — built before the LR register, because the
#  consignment row carries the invoice's number, quantity and value
# ===========================================================================
_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen "
         "Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen").split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _words(n):
    n = int(n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " Hundred" + ("" if n % 100 == 0 else " " + _words(n % 100))
    for div, name in ((10 ** 7, "Crore"), (10 ** 5, "Lakh"), (1000, "Thousand")):
        if n >= div:
            return _words(n // div) + f" {name}" + ("" if n % div == 0 else " " + _words(n % div))
    return ""


def _bank(sup):
    raw = sup.bank if isinstance(sup.bank, str) else ""
    parts = [p.strip() for p in raw.split("·")]
    if len(parts) == 3:
        return {"name": parts[0], "account_no": parts[1], "ifsc": parts[2],
                "branch": (sup.address or "").split(",")[-1].strip()}
    return {"name": None, "account_no": None, "ifsc": None, "branch": None}


def _invoice_data(ctx, sup, wh, number, date, items, po=None, lr=None, terms_days=30,
                  notes=None, eway=True):
    """A supplier invoice in the canonical schema, arithmetic exact."""
    biz = ctx["biz"]
    d = empty_invoice()
    d["template_key"] = sup.name.lower().replace(" ", "_")
    d["supplier"].update(name=sup.name, legal_name=sup.name, gstin=sup.gstin, pan=sup.pan,
                         state=sup.state, state_code=sup.state_code, address=sup.address,
                         phone=sup.phone, email=sup.email, bank=_bank(sup))
    d["buyer"].update(name=biz.legal_name or biz.name, gstin=biz.gstin, pan=biz.pan,
                      state=biz.state, state_code=biz.state_code,
                      address=f"{wh.name}, {wh.address}, {wh.city} - {wh.pincode}, {wh.state}")
    date_d = dt.datetime.strptime(date, "%Y-%m-%d")
    d["invoice"].update(number=number, date=date,
                        due_date=_iso(date_d + dt.timedelta(days=terms_days)),
                        terms=f"{terms_days} Days", agent="Direct",
                        destination=wh.city)
    if eway:
        d["invoice"]["eway_bill"] = str(random.randint(3 * 10 ** 11, 9 * 10 ** 11))
    if po:
        d["invoice"].update(order_no=po.po_no, order_date=po.po_date, agent=po.agent or "Direct")
    if lr:
        d["invoice"].update(lr_no=lr["lr_no"], lr_date=lr["lr_date"], transporter=lr["transport"])
    lines, taxable, qty = [], 0.0, 0.0
    for i, it in enumerate(items, 1):
        value = round(it["qty"] * it["rate"], 2)
        lines.append({"sr": i, "barcode": None, "description": it["description"],
                      "brand": it.get("brand"), "design": it.get("design"),
                      "size": it.get("size"), "hsn": it["hsn"], "qty": it["qty"],
                      "uom": it.get("uom") or "PCS", "mrp": it.get("mrp"), "rate": it["rate"],
                      "discount_pct": 0.0, "discount_amount": 0.0,
                      "taxable_value": value, "amount": value})
        taxable += value
        qty += it["qty"]
    taxable = round(taxable, 2)
    intra = (sup.gstin or "")[:2] == (biz.gstin or "")[:2]
    tx = d["taxes"]
    if intra:
        half = round(taxable * GST / 200, 2)
        tx.update(cgst_rate=GST / 2, cgst_amount=half, sgst_rate=GST / 2, sgst_amount=half)
        tax = round(half * 2, 2)
    else:
        tax = round(taxable * GST / 100, 2)
        tx.update(igst_rate=GST, igst_amount=tax)
    grand = float(round(taxable + tax))
    tx["round_off"] = round(grand - taxable - tax, 2)
    d["line_items"] = lines
    d["totals"].update(total_qty=qty, sub_total=taxable, taxable_total=taxable,
                       tax_total=tax, grand_total=grand,
                       amount_in_words=f"Rupees {_words(grand)} only")
    d["meta"]["notes"] = notes
    d["meta"]["tax_mode"] = "intra_state" if intra else "inter_state"
    return d


def _items_from_po(po_entry, share=1.0, max_lines=14):
    """Invoice lines for what the supplier is billing against an order: one line
    per size, at the order's rate."""
    out = []
    for l in po_entry["lines"]:
        for size, q in l["_per"].items():
            q = max(1, int(round(q * share)))
            out.append({"description": l["particulars"], "brand": l["brand"], "design": None,
                        "size": None if size == "Free" else size, "hsn": l["hsn"],
                        "qty": q, "rate": l["rate"], "mrp": l["_mrp"]})
    return out[:max_lines]


def _items_from_purchase(p):
    return [{"description": l.description, "brand": l.brand, "design": l.design_no,
             "size": l.size, "hsn": l.hsn, "qty": l.qty, "uom": l.uom, "rate": l.rate,
             "mrp": l.mrp} for l in p.lines]


# The invoices: (key, stage, supplier, warehouse, invoice no, days ago, PO key or
# base GRN invoice no, LR plan).  LR plan: (transport idx | "courier", received?)
INVOICE_PLAN = [
    # to review — read by the vision model, one slip each for the reviewer to catch
    dict(key="s02", stage="review", sup="Sample Supplier 02", wh="WH-B",
         no="S02/26-27/1187", days=6, po="po02", lr=(1, True), misread="amount",
         file="IMG_20260929_0001.png"),
    dict(key="s09", stage="review", sup="Sample Supplier 09", wh="WH-A",
         no="S09/26-27/2291", days=4, po=None, lr=(4, True), misread="qty",
         file="invoice-2291.png"),
    # checked — confirmed, no GRN yet
    dict(key="s01", stage="checked", sup="Sample Supplier 01", wh="WH-A",
         no="S01/26-27/0412", days=9, po="po01", lr=(0, True),
         file="scan_0412.png"),
    dict(key="po06", stage="checked", sup="Sample Supplier 11", wh="WH-A",
         no="S11/INV/0782", days=7, po="po06", lr=(4, True), lr_qty_off=4,
         file="INV-0782.png"),
    # receiving — GRN in draft
    dict(key="po03", stage="receiving", sup="Sample Supplier 06", wh="WH-B",
         no="S06/26-27/1564", days=8, po="po03", lr=(2, True), short=True, share=0.55,
         file="invoice_1564.png"),
    dict(key="draft_grn", stage="receiving", grn=("draft", 0), lr=(3, True),
         file="invoice-draft-grn.png"),
    # in stock — posted
    dict(key="posted_1", stage="stock", grn=("posted", 0), lr=(0, True),
         file="scan_posted_1.png"),
    dict(key="posted_2", stage="stock", grn=("posted", 1), lr=(3, True),
         file="scan_posted_2.png"),
    dict(key="s03", stage="stock", sup="Sample Supplier 03", wh="WH-A",
         no="S03/1043/26-27", days=16, po="po04", lr=(0, True), short=True,
         file="invoice_1043.png"),
]


def _base_grn(db, ctx, kind, n):
    """One of seed_base's GRNs, chosen by shape rather than by number: the n-th
    newest draft / posted receipt not already used, or ("posted", "old") — the
    posted one nearest to ninety days back."""
    used = ctx.setdefault("used_grns", set())
    P = models.Purchase
    rows = [p for p in db.query(P).filter(P.status == kind, P.document_id.is_(None))
            .order_by(P.id.desc()).all() if p.id not in used and p.lines]
    if n == "old":
        target = _iso(_when(90))
        rows.sort(key=lambda p: abs((dt.datetime.strptime(p.invoice_date, "%Y-%m-%d")
                                     - dt.datetime.strptime(target, "%Y-%m-%d")).days))
        n = 0
    if n >= len(rows):
        return None
    used.add(rows[n].id)
    return rows[n]


def _plan_invoices(db, ctx, pos):
    """Resolve each planned invoice to its supplier, warehouse, lines and dates."""
    out = []
    for spec in INVOICE_PLAN:
        s = dict(spec)
        if "grn" in s:
            p = _base_grn(db, ctx, *s["grn"])
            if not p:
                print("seed_purchasing: no base GRN for", s["grn"], "- skipped")
                continue
            s.update(purchase=p, sup_row=p.supplier, wh_row=p.warehouse,
                     no=p.invoice_number, date=p.invoice_date, items=_items_from_purchase(p),
                     po_entry=None)
        else:
            po_entry = pos.get(s["po"]) if s.get("po") else None
            date = _iso(_when(s["days"]))
            if po_entry:
                items = _items_from_po(po_entry, share=s.get("share", 1.0))
            else:                                    # no order on file for this one
                items = []
                for desc, cat, hsn, rate, mrp, brand, sizes, colours, mat, _ in \
                        _supplier_products(s["sup"]):
                    for c in colours:
                        items.append({"description": f"{desc} {c.upper()}", "brand": brand,
                                      "design": f"SD-{random.randint(100, 999)}", "size": None,
                                      "hsn": hsn, "qty": random.choice([20, 24, 30]),
                                      "rate": rate, "mrp": mrp})
            s.update(purchase=None, sup_row=ctx["sups"][s["sup"]], wh_row=ctx["whs"][s["wh"]],
                     date=date, items=items, po_entry=po_entry)
        out.append(s)
    return out


# ===========================================================================
#  LR Entry register
# ===========================================================================
_CHARGE_SETS = [
    {"L.R. Charge": 15, "H.C.": 10, "S.T. Charge": 20},
    {"L.R. Charge": 20, "Door Delivery": 60},
    {"H.C.": 20, "Insurance": 35, "S.T. Charge": 20},
    {"L.R. Charge": 15, "A.O.C.": 25},
]
_LR_PREFIX = {0: "ST1", 1: "ST2", 2: "ST3", 3: "ST4", 4: "ST5"}


def _lr_number(tr):
    return f"{_LR_PREFIX.get(tr, 'SC1')}-{random.randint(10000, 99999)}"


def _make_lr(db, wid, source, data, received_by=None, when=None):
    """One register row through routers/lr's own write funnel (_apply: coercion,
    date normalisation, paid/to-pay spelling, the transport/agent masters). A
    manual row is held to the same purchase-order check the form is."""
    if source == "manual":
        lr_router.check_purchase_order(db, data.get("purchase_order_id"), required=True)
    e = models.LREntry(entry_source=source, warehouse_id=wid)
    lr_router._apply(e, data, db)
    e.lr_entry_no = lr_svc.next_entry_no(db)
    e.received_by = received_by
    if when:
        e.created_at = when
    db.add(e)
    db.flush()
    return e


def _freight(tr, bundles):
    base_fr = 180 * bundles + random.choice([0, 25, 50])
    charges = dict(random.choice(_CHARGE_SETS))
    return base_fr, charges, round(base_fr + sum(charges.values()), 2)


def _lr_entries(db, ctx, pos):
    """The register. Rows for the planned invoices first (their LR number is
    printed on the invoice too), then consignments with no invoice yet."""
    plans = _plan_invoices(db, ctx, pos)
    ctx["plans"] = plans
    lrs = {}
    for s in plans:
        tr, received = s["lr"]
        inv_date = dt.datetime.strptime(s["date"], "%Y-%m-%d")
        lr_date = inv_date + dt.timedelta(days=1)
        recv = lr_date + dt.timedelta(days=random.choice([2, 3, 4]))
        qty = sum(i["qty"] for i in s["items"])
        bundles = max(1, int(qty // 40) + 1)
        fr, charges, total = _freight(tr, bundles)
        sup, wh = s["sup_row"], s["wh_row"]
        po = s["po_entry"]["po"] if s.get("po_entry") else None
        lr = {"lr_no": _lr_number(tr), "lr_date": _iso(lr_date), "transport": TRANSPORTS[tr]}
        s["lr_row"] = lr
        # the invoice's own value, so the cross-fill confirms instead of flagging
        data = _invoice_data(ctx, sup, wh, s["no"], s["date"], s["items"], po=po, lr=lr)
        s["data"] = data
        row = dict(
            lr_mode="Transport", lr_no=lr["lr_no"], lr_date=lr["lr_date"],
            recv_date=_iso(recv) if received else None,
            lr_entry_date=_iso(recv if received else lr_date + dt.timedelta(days=1)),
            supplier_name=sup.name, agent=(po.agent if po else "Direct") or "Direct",
            agent_commission=2.0 if po and po.agent and po.agent != "Direct" else None,
            transport=lr["transport"], auto_transfer_location="NONE",
            purchase_manager=random.choice(PURCHASERS), stock_holding_days=90,
            additional_margin=random.choice([None, 5.0]),
            bundle=bundles, boxes=max(1, bundles // 2),
            qty=qty + s.get("lr_qty_off", 0),
            amount=data["totals"]["grand_total"], inv_no=s["no"], inv_date=s["date"],
            paid_topay=random.choice(["TOPAY", "TOPAY", "PAID"]),
            freight_amount=fr, freight_total=total, freight_charges=charges,
            item=" ".join(s["items"][0]["description"].split()[:3]).title(),
            purchase_order_id=po.id if po else None)
        source = "manual" if po else "import"
        e = _make_lr(db, wh.id, source, row,
                     received_by=random.choice(RECEIVERS) if received else None,
                     when=lr_date + dt.timedelta(days=1, hours=random.randint(1, 5)))
        lrs[s["key"]] = e

    # consignments with no invoice keyed yet
    extra = [
        # (supplier, wh, PO key, days ago (LR date), received?, transport, qty, value,
        #  bundles, mode, item, transfer)
        ("Sample Supplier 06", "WH-B", "po03", 2, False, 2, 96, 58200, 3, "Transport",
         "Sample Piece 011", "Sample Store 4"),
        ("Sample Supplier 04", "WH-C", "po05", 1, False, 3, 24, 118400, 2,
         "Hand Delivery", "Sample Piece 007", "NONE"),
        ("Sample Supplier 05", "WH-C", None, 3, False, 4, 16, 96500, 2, "Transport",
         "Sample Piece 008", "NONE"),
        ("Sample Supplier 12", "WH-B", None, 12, True, 1, 48, 41300, 2, "Transport",
         "Sample Piece 021", "NONE"),
        ("Sample Supplier 12", "WH-A", None, 6, True, None, 36, 9800, 1, "Courier",
         "Sample Piece 022", "NONE"),
        ("Sample Supplier 07", "WH-A", None, 85, True, 2, 54, 36290, 2, "Transport",
         "Sample Piece 013", "NONE"),
    ]
    # two register rows are the consignments seed_base GRNs came in on: the next
    # draft not already used above, and a posted one about three months old
    base_tie = {3: ("draft", 0), 5: ("posted", "old")}
    for n, (sup_name, wh_code, po_key, days, received, tr, qty, value, bundles, mode, item,
            transfer) in enumerate(extra):
        sup, wh = ctx["sups"][sup_name], ctx["whs"][wh_code]
        po = pos[po_key]["po"] if po_key else None
        inv_no = inv_date = None
        bp = _base_grn(db, ctx, *base_tie[n]) if n in base_tie else None
        if bp is not None:
            sup, wh = bp.supplier, bp.warehouse
            item = " ".join((bp.lines[0].description or "").split()[:3]).title() if bp.lines else item
        if po_key == "po03":           # the second lot: what the first did not bring
            first = sum(i["qty"] for p_ in ctx["plans"] if p_["key"] == "po03" for i in p_["items"])
            ordered = sum(float(l.qty or 0) for l in po.lines)
            qty = max(12, int(ordered - first))
            value = float(round(qty * (po.total / ordered) * (1 + GST / 100)))
        if bp is not None:              # the consignment a seed_base GRN came in on
            inv_no, inv_date = bp.invoice_number, bp.invoice_date
            qty = int(sum(float(l.qty or 0) for l in bp.lines))
            value = bp.grand_total or value
            days = max(1, (TODAY - dt.datetime.strptime(bp.invoice_date, "%Y-%m-%d")).days - 1)
        lr_date = _when(days)
        recv = lr_date + dt.timedelta(days=3)
        courier = tr is None
        fr, charges, total = (120, {"Docket": 30}, 150) if courier else _freight(tr, bundles)
        if mode == "Hand Delivery":
            fr, charges, total = None, None, None
        row = dict(
            lr_mode=mode, lr_no=(f"SC1-{random.randint(1000000, 9999999)}" if courier
                                 else _lr_number(tr) if mode != "Hand Delivery"
                                 else f"HD-{random.randint(100, 999)}"),
            lr_date=_iso(lr_date), recv_date=_iso(recv) if received else None,
            lr_entry_date=_iso(recv if received else lr_date),
            supplier_name=sup.name, agent=(po.agent if po else "Direct") or "Direct",
            transport=COURIER if courier else (None if mode == "Hand Delivery" else TRANSPORTS[tr]),
            auto_transfer_location=transfer, purchase_manager=random.choice(PURCHASERS),
            stock_holding_days=random.choice([60, 90, 120]),
            bundle=bundles, boxes=bundles if courier else max(1, bundles // 2),
            qty=qty, amount=value, inv_no=inv_no, inv_date=inv_date,
            paid_topay="NO" if mode == "Hand Delivery" else ("PAID" if courier else "TOPAY"),
            freight_amount=fr, freight_total=total, freight_charges=charges, item=item,
            purchase_order_id=po.id if po else None)
        if mode == "Hand Delivery":
            row["freight_applicable"] = False
        e = _make_lr(db, wh.id, "manual" if po else "import", row,
                     received_by=random.choice(RECEIVERS) if received else None,
                     when=lr_date + dt.timedelta(hours=8))
        lrs[f"x-{sup_name}-{days}"] = e
    db.commit()
    return lrs


# ===========================================================================
#  Invoice Entry documents
# ===========================================================================
def _invoices(db, ctx, pos, lrs):
    biz = ctx["biz"]
    made = []
    for s in ctx["plans"]:
        data = s["data"]
        sup, wh = s["sup_row"], s["wh_row"]
        inv_date = dt.datetime.strptime(s["date"], "%Y-%m-%d")
        uploaded = inv_date + dt.timedelta(days=random.choice([3, 4, 5]), hours=random.randint(2, 7))
        if uploaded > TODAY:
            uploaded = TODAY - dt.timedelta(hours=random.randint(1, 4))

        png = render_invoice(data)
        h = hashlib.sha256(png).hexdigest()
        ref = storage.save(png, f"{h[:16]}.png")
        doc = models.Document(filename=s["file"], stored_path=ref, pages=[ref],
                              content_hash=h, mime="image/png", document_type="invoice",
                              supplier_id=sup.id, warehouse_id=wh.id, status="uploaded",
                              uploaded_at=uploaded)
        db.add(doc)
        db.flush()

        # the machine's reading — exact, except where the plan gives it a slip
        reading = copy.deepcopy(data)
        if s.get("misread") == "amount" and len(reading["line_items"]) > 2:
            li = reading["line_items"][2]
            li["amount"] = li["taxable_value"] = round(li["amount"] + 500, 2)
        if s.get("misread") == "qty":
            reading["totals"]["total_qty"] = float(reading["totals"]["total_qty"]) + 10
        reading, warnings, flags, conf = normalise_and_check(
            reading, company_gstin=biz.gstin)
        if s["stage"] != "review":
            conf = round(random.uniform(0.95, 0.99), 2)
        db.add(models.Extraction(document_id=doc.id, provider="claude_vision", data=reading,
                                 confidence=conf, warnings=warnings, field_flags=flags,
                                 raw_text="", created_at=uploaded + dt.timedelta(minutes=1)))
        if s["stage"] == "review":
            doc.status = "needs_review"
        else:
            # checked by a person: the corrected copy, as POST /confirm stores it
            db.add(models.Extraction(document_id=doc.id, provider="human", data=data,
                                     confidence=1.0, warnings=[], field_flags={},
                                     is_correction=True,
                                     created_at=uploaded + dt.timedelta(minutes=random.randint(8, 40))))
            for it in data["line_items"]:
                db.add(models.LineItem(document_id=doc.id, description=it["description"],
                                       hsn=it["hsn"], qty=it["qty"], uom=it["uom"],
                                       rate=it["rate"], amount=it["amount"]))
            doc.status = "confirmed"
        db.flush()
        db.refresh(doc)
        # the register row this invoice travelled under — the app's own cross-fill
        lr_link.fill_lr_from_invoice(db, doc.latest_extraction.data, doc.id)

        p = s.get("purchase")
        if p is not None:                                   # an existing base GRN
            p.document_id = doc.id
            p.taxable_total = data["totals"]["taxable_total"]
            p.tax_total = data["totals"]["tax_total"]
            p.grand_total = data["totals"]["grand_total"]
            if p.status == "posted":
                doc.status = "posted"
            inv._publish_grn_no(db, doc, p.grn_no)
        elif s["stage"] in ("receiving", "stock"):
            p = inv.build_grn_from_document(db, doc)
            p.created_at = uploaded + dt.timedelta(hours=1)
            db.flush()
            if s.get("short"):
                _record_shortages(db, p, uploaded + dt.timedelta(hours=2))
            if s["stage"] == "stock":
                res = inv.post_grn(db, p)
                if not res.get("ok"):
                    raise RuntimeError(f"post failed for {s['no']}: {res.get('error')}")
                posted = uploaded + dt.timedelta(hours=5)
                p.posted_at = posted
                db.query(models.StockMovement).filter(
                    models.StockMovement.ref_type == "purchase",
                    models.StockMovement.ref_id == p.id).update(
                    {"created_at": posted}, synchronize_session=False)
                if s.get("short"):
                    _settle_shortages(db, p, posted)
        db.commit()
        made.append((s, doc))
    return made


def _record_shortages(db, p, when):
    """What the cartons did not hold, recorded at the dock before posting."""
    lines = sorted(p.lines, key=lambda l: -float(l.qty or 0))   # the big cartons
    plan = [(0, "short", 4, "Short packed", "Carton 2 of 3 had 8 instead of 12"),
            (1, "damaged", 2, "Wet / stained", "Water marks on the outer fold")]
    if len(lines) > 2:
        plan.append((2, "short", 3, "Not in box", None))
    for idx, kind, qty, reason, note in plan:
        if idx >= len(lines):
            continue
        # never more than a third of what the line billed
        qty = max(1, min(qty, int(float(lines[idx].qty or 0) // 3)))
        short_svc.set_line_shortages(db, lines[idx], [dict(
            kind=kind, qty=qty, reason=reason, note=note, variant=lines[idx].size)],
            by=random.choice(RECEIVERS))
        for sh in lines[idx].shortages:
            sh.recorded_at = sh.created_at = when
    db.flush()


def _settle_shortages(db, p, when):
    """After posting: the damaged pieces are let go (the supplier is replacing
    them), and the shortfall is claimed on a posted debit note."""
    for line in p.lines:
        for sh in line.shortages:
            if sh.kind == "damaged":
                short_svc.waive(db, sh, reason="Supplier replacing in next lot",
                                by="Sample Purchaser 1")
                sh.waived_at = when + dt.timedelta(days=1)
    if not short_svc.claimable(db, p):
        return
    ret = ret_svc.build_from_purchase(db, p, shortages_only=True)
    # raised and posted in two requests on screen; reload the lines between them
    db.commit()
    db.refresh(ret)
    res = ret_svc.post(db, ret, reason="Short received against invoice "
                       f"{p.invoice_number}", date=_iso(when + dt.timedelta(days=2)))
    if not res.get("ok"):
        raise RuntimeError(f"debit note failed: {res.get('error')}")
    ret.created_at = when + dt.timedelta(days=2)
    ret.posted_at = when + dt.timedelta(days=2, hours=2)
    db.flush()


# ===========================================================================
#  The invoice image
# ===========================================================================
_FONT_CACHE = {}
_FONT_FILES = {
    False: ["arial.ttf", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
    True: ["arialbd.ttf", "C:/Windows/Fonts/arialbd.ttf", "DejaVuSans-Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
}


def _font(size, bold=False):
    key = (size, bold)
    if key not in _FONT_CACHE:
        f = None
        for name in _FONT_FILES[bold]:
            try:
                f = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        _FONT_CACHE[key] = f or ImageFont.load_default(size=size)
    return _FONT_CACHE[key]


def _money(v):
    """Indian digit grouping: 1,23,456.00"""
    neg, v = v < 0, abs(float(v))
    whole, frac = f"{v:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    s = ",".join(groups + [tail]) if groups else tail
    return ("-" if neg else "") + s + "." + frac


def _fmt_date(iso):
    try:
        return dt.datetime.strptime(iso, "%Y-%m-%d").strftime("%d-%m-%Y")
    except (TypeError, ValueError):
        return iso or ""


_ACCENTS = [(28, 78, 140), (122, 31, 61), (20, 104, 84), (110, 72, 20), (64, 52, 128)]


def render_invoice(d):
    """A plain GST tax invoice, drawn from the canonical dict. Returns PNG bytes."""
    sup, buy, inv_h = d["supplier"], d["buyer"], d["invoice"]
    items, tx, tot = d["line_items"], d["taxes"], d["totals"]
    accent = _ACCENTS[sum(map(ord, sup["name"] or "")) % len(_ACCENTS)]
    W, M = 1240, 50
    row_h = 34
    H = 700 + row_h * max(len(items), 6) + 470
    img = Image.new("RGB", (W, H), (255, 255, 255))
    g = ImageDraw.Draw(img)
    ink, grey, line = (25, 25, 30), (95, 95, 105), (150, 150, 160)

    def text(x, y, s, size=18, bold=False, fill=ink, anchor="la"):
        g.text((x, y), str(s), font=_font(size, bold), fill=fill, anchor=anchor)

    # title band
    g.rectangle([M, 30, W - M, 70], fill=accent)
    text(W // 2, 50, "TAX INVOICE", 24, True, (255, 255, 255), "mm")
    text(W - M - 10, 50, "Original for Recipient", 14, False, (230, 230, 240), "rm")

    # supplier
    y = 92
    text(M, y, (sup["name"] or "").upper(), 34, True, accent)
    y += 46
    for s in (sup["address"], f"Phone: {sup['phone'] or ''}    Email: {sup['email'] or ''}",
              f"GSTIN: {sup['gstin']}    PAN: {sup['pan'] or ''}    State: {sup['state']} ({sup['state_code']})"):
        text(M, y, s or "", 17, False, grey if s is not sup["address"] else ink)
        y += 26
    y += 8
    g.line([M, y, W - M, y], fill=line, width=2)

    # buyer + invoice particulars
    top = y + 12
    text(M, top, "Bill To / Ship To", 15, True, grey)
    text(M, top + 24, buy["name"] or "", 20, True)
    addr = buy["address"] or ""
    cut = addr.find(",", 40)
    parts = [addr[:cut + 1], addr[cut + 1:].strip()] if cut > 0 else [addr]
    yy = top + 52
    for s in parts:
        text(M, yy, s, 16)
        yy += 24
    text(M, yy, f"GSTIN: {buy['gstin']}    State: {buy['state']} ({buy['state_code']})", 16)
    kv = [("Invoice No", inv_h["number"]), ("Invoice Date", _fmt_date(inv_h["date"])),
          ("Due Date", _fmt_date(inv_h.get("due_date"))), ("Terms", inv_h.get("terms")),
          ("Order No", f"{inv_h.get('order_no') or '-'}"
                       + (f"  dt {_fmt_date(inv_h.get('order_date'))}" if inv_h.get("order_no") else "")),
          ("LR No", f"{inv_h.get('lr_no') or '-'}"
                    + (f"  dt {_fmt_date(inv_h.get('lr_date'))}" if inv_h.get("lr_no") else "")),
          ("Transport", inv_h.get("transporter") or "-"),
          ("E-Way Bill", inv_h.get("eway_bill") or "-")]
    bx = 700
    g.rectangle([bx - 14, top - 4, W - M, top + 22 * len(kv) + 8], outline=line, width=1)
    for i, (k, v) in enumerate(kv):
        text(bx, top + 4 + i * 22, k, 15, True, grey)
        text(bx + 130, top + 4 + i * 22, f": {v or '-'}", 15, k == "Invoice No")
    y = max(yy + 34, top + 22 * len(kv) + 24)

    # line table
    cols = [("S.No", 60, "c"), ("Description of Goods", 380, "l"), ("HSN", 80, "c"),
            ("Size", 80, "c"), ("Qty", 80, "r"), ("UOM", 70, "c"), ("Rate", 140, "r"),
            ("Amount", W - 2 * M - 890, "r")]
    g.rectangle([M, y, W - M, y + 38], fill=(238, 240, 246), outline=line)
    x = M
    for name, w, al in cols:
        ax = {"l": x + 8, "c": x + w // 2, "r": x + w - 8}[al]
        text(ax, y + 19, name, 16, True, ink, {"l": "lm", "c": "mm", "r": "rm"}[al])
        x += w
    y += 38
    t0 = y
    for it in items:
        vals = [it["sr"], (it["description"] or "")[:34], it["hsn"], it.get("size") or "-",
                f"{it['qty']:g}", it.get("uom") or "PCS", _money(it["rate"]), _money(it["amount"])]
        x = M
        for (name, w, al), v in zip(cols, vals):
            ax = {"l": x + 8, "c": x + w // 2, "r": x + w - 8}[al]
            text(ax, y + row_h // 2, v, 16, False, ink, {"l": "lm", "c": "mm", "r": "rm"}[al])
            x += w
        y += row_h
    y = max(y, t0 + row_h * 6) + 6
    x = M
    for name, w, al in cols:
        g.line([x, t0 - 38, x, y], fill=line)
        x += w
    g.line([W - M, t0 - 38, W - M, y], fill=line)
    g.line([M, y, W - M, y], fill=line, width=2)
    text(M + 448, y + 20, "Total", 17, True, ink, "rm")
    text(M + 60 + 380 + 80 + 80 + 80 - 8, y + 20, f"{tot['total_qty']:g}", 17, True, ink, "rm")
    text(W - M - 8, y + 20, _money(tot["taxable_total"]), 17, True, ink, "rm")
    y += 40
    g.line([M, y, W - M, y], fill=line)

    # tax summary
    rows = [("Taxable Value", tot["taxable_total"])]
    if tx.get("igst_amount"):
        rows.append((f"IGST @ {tx['igst_rate']:g}%", tx["igst_amount"]))
    else:
        rows += [(f"CGST @ {tx['cgst_rate']:g}%", tx["cgst_amount"]),
                 (f"SGST @ {tx['sgst_rate']:g}%", tx["sgst_amount"])]
    rows.append(("Round Off", tx.get("round_off") or 0))
    ty = y + 16
    for k, v in rows:
        text(W - M - 230, ty, k, 17, False, grey, "rm")
        text(W - M - 8, ty, _money(v), 17, False, ink, "rm")
        ty += 28
    g.rectangle([W - M - 470, ty, W - M, ty + 40], fill=accent)
    text(W - M - 230, ty + 20, "GRAND TOTAL", 19, True, (255, 255, 255), "rm")
    text(W - M - 8, ty + 20, "Rs. " + _money(tot["grand_total"]), 19, True, (255, 255, 255), "rm")

    # words, HSN tax line, bank
    by = y + 16
    text(M, by, "Amount in words:", 15, True, grey)
    words = tot.get("amount_in_words") or ""
    cut = words.rfind(" ", 0, 52) if len(words) > 52 else -1
    if cut > 0:
        text(M, by + 22, words[:cut], 16, True)
        text(M, by + 44, words[cut + 1:], 16, True)
    else:
        text(M, by + 22, words, 16, True)
    bank = sup.get("bank") or {}
    text(M, by + 82, "Bank Details", 15, True, grey)
    text(M, by + 104, f"{bank.get('name') or ''}   A/c No: {bank.get('account_no') or ''}", 16)
    text(M, by + 126, f"IFSC: {bank.get('ifsc') or ''}   Branch: {bank.get('branch') or ''}", 16)

    fy = max(ty + 70, by + 170)
    g.line([M, fy, W - M, fy], fill=line)
    for i, s in enumerate(["Terms: Goods once sold will not be taken back. Interest @ 18% p.a. "
                           "after due date.", "Subject to " + ((sup["address"] or "").split(",")[-1].strip()
                                                               or "local") + " jurisdiction. E.&O.E."]):
        text(M, fy + 14 + i * 22, s, 14, False, grey)
    text(W - M, fy + 18, f"For {sup['name']}", 17, True, ink, "ra")
    text(W - M, fy + 96, "Authorised Signatory", 15, False, grey, "ra")
    H = min(H, fy + 170)
    img = img.crop((0, 0, W, H))
    g = ImageDraw.Draw(img)
    text(W // 2, H - 24, "This is a computer generated invoice", 13, False, grey, "mm")

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ===========================================================================
#  summary
# ===========================================================================
def _report(db, docs):
    from collections import Counter
    po_counts = Counter(p.status for p in db.query(models.PurchaseOrder).all())
    lrs = db.query(models.LREntry).all()
    stage = {}
    for s, doc in docs:
        stage.setdefault(s["stage"], []).append(doc.id)
    shorts = Counter(short_svc.status_of(db, sh) for sh in db.query(models.GrnShortage).all())
    print({
        "purchase_orders": dict(po_counts),
        "lr_entries": {"total": len(lrs),
                       "received": sum(1 for e in lrs if e.received_by),
                       "pending": sum(1 for e in lrs if not e.received_by),
                       "linked_to_invoice": sum(1 for e in lrs if e.matched),
                       "against_po": sum(1 for e in lrs if e.purchase_order_id),
                       "manual": sum(1 for e in lrs if e.entry_source == "manual"),
                       "import": sum(1 for e in lrs if e.entry_source == "import")},
        "invoice_documents": {k: len(v) for k, v in stage.items()},
        "grns": dict(Counter(p.status for p in db.query(models.Purchase).all())),
        "shortages": dict(shorts),
        "debit_notes": db.query(models.PurchaseReturn).count(),
    })


if __name__ == "__main__":
    session = SessionLocal()
    try:
        run(session)
    finally:
        session.close()
