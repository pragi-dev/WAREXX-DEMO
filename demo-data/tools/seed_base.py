"""Build the static demo's sample business in a SCRATCH database.

    set ESSA_DATABASE_URL=sqlite:///C:/path/to/demo-build/demo.db
    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\seed_base.py

The static demo (frontend/demo) has no server: everything it shows was recorded
from a real server running on the database this script builds (see
record.mjs). So this is the one place the demo's story is written — a fictional
retailer with three warehouses, six stores and six months of trade — and it is
written through the application's own services, exactly as the screens would,
so every recorded answer is a real one.

It refuses to touch any database that is not clearly a scratch file: it drops
every table before it starts.
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
             "folder. This script DROPS EVERY TABLE.")

random.seed(7)          # the same story on every build

from app import seed as base_seed                                  # noqa: E402
from app import models                                             # noqa: E402
from app.database import SessionLocal                              # noqa: E402
from app.services import catalogues as cat_svc                     # noqa: E402
from app.services import inventory as inv                          # noqa: E402
from app.services import outward as out_svc                        # noqa: E402
from app.services import stock_locations as stock_loc              # noqa: E402

TODAY = dt.datetime.utcnow().replace(hour=6, minute=0, second=0, microsecond=0)

COMPANY = dict(
    code="SMPL", name="Sample Company", legal_name="Sample Company Pvt Ltd",
    gstin="33AAACS0000A1Z5", pan="AAACS0000A", address="1, ABC Area, Main Road",
    city="Sample City", state="Tamil Nadu", state_code="33", country="India",
    pincode="600001", phone="+91 90000 00001", email="accounts@sample.example")

# (name, code, catalogue, city, district, pincode, address, contact, phone)
WAREHOUSES = [
    ("Sample Warehouse A", "WH-A", "GARMENTS", "Sample City", "Sample District",
     "600010", "10, ABC Area, Industrial Estate", "Sample Contact 1", "+91 90000 00010"),
    ("Sample Warehouse B", "WH-B", "GARMENTS", "XYZ Town", "XYZ District",
     "600020", "20, PQR Area, Main Road", "Sample Contact 2", "+91 90000 00020"),
    ("Sample Warehouse C", "WH-C", "SILKS", "LMN Nagar", "LMN District",
     "600030", "30, DEF Area, Market Street", "Sample Contact 3", "+91 90000 00030"),
]
# (name, code, warehouse code, city, pincode, floors)
STORES = [
    ("Sample Store 1", "ST-01", "WH-A", "Sample City", "600011", ["Ground Floor", "First Floor"]),
    ("Sample Store 2", "ST-02", "WH-A", "Sample City", "600012", ["Ground Floor"]),
    ("Sample Store 3", "ST-03", "WH-B", "XYZ Town", "600021", ["Ground Floor", "First Floor"]),
    ("Sample Store 4", "ST-04", "WH-B", "XYZ Town", "600022", ["Ground Floor"]),
    ("Sample Store 5", "ST-05", "WH-C", "LMN Nagar", "600031", ["Ground Floor", "First Floor"]),
    ("Sample Store 6", "ST-06", "WH-C", "LMN Nagar", "600032", ["Ground Floor", "First Floor"]),
]
# (name, gstin, state, state_code, city, phone, email, bank)
SUPPLIERS = [
    ("Sample Supplier 01", "33AABCS1001A1Z1", "Tamil Nadu", "33", "ABC Area", "+91 90000 00101", "supplier01@sample.example", "Sample Bank · 0000 0000 1001 · SMPL0000001"),
    ("Sample Supplier 02", "33AABCS1002A1Z2", "Tamil Nadu", "33", "ABC Area", "+91 90000 00102", "supplier02@sample.example", "Sample Bank · 0000 0000 1002 · SMPL0000002"),
    ("Sample Supplier 03", "29AABCS1003A1Z3", "Karnataka", "29", "ABC Area", "+91 90000 00103", "supplier03@sample.example", "Sample Bank · 0000 0000 1003 · SMPL0000003"),
    ("Sample Supplier 04", "32AABCS1004A1Z4", "Kerala", "32", "ABC Area", "+91 90000 00104", "supplier04@sample.example", "Sample Bank · 0000 0000 1004 · SMPL0000004"),
    ("Sample Supplier 05", "27AABCS1005A1Z5", "Maharashtra", "27", "ABC Area", "+91 90000 00105", "supplier05@sample.example", "Sample Bank · 0000 0000 1005 · SMPL0000005"),
    ("Sample Supplier 06", "24AABCS1006A1Z6", "Gujarat", "24", "ABC Area", "+91 90000 00106", "supplier06@sample.example", "Sample Bank · 0000 0000 1006 · SMPL0000006"),
    ("Sample Supplier 07", "33AABCS1007A1Z7", "Tamil Nadu", "33", "ABC Area", "+91 90000 00107", "supplier07@sample.example", "Sample Bank · 0000 0000 1007 · SMPL0000007"),
    ("Sample Supplier 08", "33AABCS1008A1Z8", "Tamil Nadu", "33", "ABC Area", "+91 90000 00108", "supplier08@sample.example", "Sample Bank · 0000 0000 1008 · SMPL0000008"),
    ("Sample Supplier 09", "29AABCS1009A1Z9", "Karnataka", "29", "ABC Area", "+91 90000 00109", "supplier09@sample.example", "Sample Bank · 0000 0000 1009 · SMPL0000009"),
    ("Sample Supplier 10", "32AABCS1010A1Z0", "Kerala", "32", "ABC Area", "+91 90000 00110", "supplier10@sample.example", "Sample Bank · 0000 0000 1010 · SMPL0000010"),
    ("Sample Supplier 11", "27AABCS1011A1Z1", "Maharashtra", "27", "ABC Area", "+91 90000 00111", "supplier11@sample.example", "Sample Bank · 0000 0000 1011 · SMPL0000011"),
    ("Sample Supplier 12", "24AABCS1012A1Z2", "Gujarat", "24", "ABC Area", "+91 90000 00112", "supplier12@sample.example", "Sample Bank · 0000 0000 1012 · SMPL0000012"),
]
# (description, category, hsn, rate, mrp, brand, sizes, colours, material, supplier index)
PRODUCTS = [
    ("SAMPLE PIECE 001", "MENS-SHIRT", "6205", 420, 1199, "Sample Brand A", ["38", "40", "42", "44"], ["White", "Sky Blue"], "Cotton", 0),
    ("SAMPLE PIECE 002", "MENS-SHIRT", "6205", 380, 999, "Sample Brand A", ["M", "L", "XL"], ["Red Check", "Navy Check"], "Cotton", 0),
    ("SAMPLE PIECE 003", "ESSA MENS-T-SHIRT", "6109", 165, 499, "Sample Brand B", ["S", "M", "L", "XL"], ["Black", "Olive"], "Cotton Jersey", 1),
    ("SAMPLE PIECE 004", "ESSA MENS-T-SHIRT", "6105", 240, 699, "Sample Brand B", ["M", "L", "XL"], ["Maroon", "Grey Melange"], "Pique Cotton", 1),
    ("SAMPLE PIECE 005", "LADIES-CHUDITHAR", "6204", 310, 899, "Sample Brand C", ["S", "M", "L", "XL"], ["Mustard", "Teal"], "Cotton", 2),
    ("SAMPLE PIECE 006", "LADIES-T-SHIRT", "6109", 150, 449, "Sample Brand B", ["S", "M", "L"], ["Pink", "Lavender"], "Cotton Jersey", 1),
    ("SAMPLE PIECE 007", "LADIES-SAREE", "5007", 6200, 14500, "Sample Brand D", ["Free"], ["Arakku Red", "Peacock Green"], "Pure Silk", 3),
    ("SAMPLE PIECE 008", "LADIES-SAREE", "5007", 5400, 12900, "Sample Brand E", ["Free"], ["Royal Blue", "Gold"], "Silk", 4),
    ("SAMPLE PIECE 009", "LADIES-SAREE", "5007", 2100, 5200, "Sample Brand D", ["Free"], ["Rani Pink", "Mint"], "Soft Silk", 3),
    ("SAMPLE PIECE 010", "MENS-DHOTI SET", "5007", 1150, 2800, "Sample Brand D", ["Free"], ["Cream"], "Silk", 3),
    ("SAMPLE PIECE 011", "MENS-PANT BIT", "6203", 560, 1599, "Sample Brand F", ["30", "32", "34", "36"], ["Indigo", "Black"], "Denim", 5),
    ("SAMPLE PIECE 012", "LADIES-CHUDITHAR", "6204", 520, 1499, "Sample Brand F", ["26", "28", "30"], ["Ice Blue"], "Denim", 5),
    ("SAMPLE PIECE 013", "JERKIN", "6110", 640, 1799, "Sample Brand G", ["M", "L", "XL"], ["Charcoal", "Camel"], "Wool Blend", 6),
    ("SAMPLE PIECE 014", "LADIES-GOWN", "6204", 1250, 3499, "Sample Brand H", ["S", "M", "L"], ["Wine", "Emerald"], "Georgette", 7),
    ("SAMPLE PIECE 015", "LADIES-SAREE", "5407", 690, 1899, "Sample Brand I", ["Free"], ["Coral", "Navy"], "Georgette", 8),
    ("SAMPLE PIECE 016", "KIDS-MIDI", "6209", 210, 599, "Sample Brand J", ["2-3Y", "4-5Y", "6-7Y"], ["Yellow", "Peach"], "Cotton", 9),
    ("SAMPLE PIECE 017", "KIDS-T-SHIRT", "6109", 120, 349, "Sample Brand J", ["4-5Y", "6-7Y", "8-9Y"], ["Blue", "Red"], "Cotton", 9),
    ("SAMPLE PIECE 018", "PATTU PAVADAI", "5007", 780, 1999, "Sample Brand D", ["2-3Y", "4-5Y", "6-7Y"], ["Magenta"], "Silk", 3),
    ("SAMPLE PIECE 019", "MENS-KURTA SET", "6203", 890, 2499, "Sample Brand K", ["M", "L", "XL"], ["Ivory", "Bottle Green"], "Cotton Silk", 10),
    ("SAMPLE PIECE 020", "LADIES-CHUDITHAR", "6204", 1350, 3799, "Sample Brand K", ["S", "M", "L"], ["Ruby", "Turquoise"], "Rayon", 10),
    ("SAMPLE PIECE 021", "LADIES-SAREE", "5208", 820, 2199, "Sample Brand L", ["Free"], ["Off White", "Rust"], "Handloom Cotton", 11),
    ("SAMPLE PIECE 022", "DHOTI SET", "5208", 260, 699, "Sample Brand L", ["Free"], ["White"], "Cotton", 11),
    ("SAMPLE PIECE 023", "LADIES-AARAM", "6208", 280, 799, "Sample Brand C", ["M", "L", "XL"], ["Lilac", "Sky"], "Cotton", 2),
    ("SAMPLE PIECE 024", "MENS-BELT", "4203", 230, 699, "Sample Brand H", ["Free"], ["Brown", "Black"], "Leather", 7),
]


def _addr(city, state="Tamil Nadu", state_code="33"):
    return dict(city=city, district=city, state=state, state_code=state_code, country="India")


def main():
    base_seed.main(reset=True, empty=True)
    db = SessionLocal()
    try:
        _business(db)
        cats = _catalogues(db)
        whs = _warehouses(db, cats)
        _stores(db, whs)
        sups = _suppliers(db)
        _receipts(db, whs, sups)
        _transfers(db, whs)
        _detail_products(db)
        db.commit()
        print({t: db.query(m).count() for t, m in [
            ("warehouses", models.Warehouse), ("stores", models.Store),
            ("tills", models.PosTerminal), ("suppliers", models.Supplier),
            ("products", models.Product), ("grns", models.Purchase),
            ("transfers", models.StockOutward)]})
    finally:
        db.close()


def _business(db):
    from app.services import businesses
    businesses.ensure_seed(db)
    biz = businesses.default_business(db)
    for k, v in COMPANY.items():
        setattr(biz, k, v)
    db.commit()


def _catalogues(db):
    cat_svc.ensure_seed(db)
    garments = cat_svc.default_catalogue(db)
    silks = db.query(models.Catalogue).filter(models.Catalogue.code == "SILKS").first()
    if not silks:
        silks = cat_svc.create(db, "SILKS", "Silks", "Silk sarees, dhotis and pavadai sets.",
                               attrs=["color", "material", "design_no", "weave", "zari", "border"])
        for attr, values in {"weave": ["Plain Weave", "Jacquard", "Twill", "Brocade"],
                             "zari": ["Pure", "Half fine", "Tested"],
                             "border": ["Temple", "Plain", "Contrast", "Zari"]}.items():
            for v in values:
                cat_svc.add_option(db, silks.id, attr, v)
    db.commit()
    return {"GARMENTS": garments.id, "SILKS": silks.id}


def _warehouses(db, cats):
    out = {}
    home = stock_loc.default_warehouse(db)
    for i, (name, code, cat, city, district, pin, address, contact, phone) in enumerate(WAREHOUSES):
        w = home if i == 0 and home else models.Warehouse()
        w.name, w.code, w.catalogue_id, w.active = name, code, cats[cat], True
        w.loc_type = "Distribution Centre"
        w.address, w.pincode, w.contact_person, w.phone = address, pin, contact, phone
        w.email = f"{code.lower().replace('-', '')}@sample.example"
        w.gstin = COMPANY["gstin"]
        for k, v in _addr(city).items():
            setattr(w, k, v)
        w.district = district
        if w.id is None:
            db.add(w)
        db.flush()
        out[code] = w
    db.commit()
    return out


def _stores(db, whs):
    for name, code, wh_code, city, pin, floors in STORES:
        s = models.Store(name=name, code=code, warehouse_id=whs[wh_code].id, active=True,
                         loc_type="Showroom" if wh_code == "WH-C" else "Retail",
                         address=f"{random.randint(10, 99)}, ABC Area, Main Road", pincode=pin,
                         contact_person=f"Sample Contact {random.randint(4, 9)}",
                         phone=f"+91 9{random.randint(100000000, 999999999)}",
                         email=f"{code.lower().replace('-', '')}@sample.example",
                         gstin=COMPANY["gstin"], **_addr(city))
        db.add(s)
        db.flush()
        for n, floor in enumerate(floors):
            f = models.Floor(store_id=s.id, name=floor, prefix=f"S{int(code[-2:])}{n + 1}",
                             sort_order=n, active=True)
            db.add(f)
            db.flush()
            for t in range(2 if n == 0 else 1):
                db.add(models.PosTerminal(store_id=s.id, floor_id=f.id, active=True,
                                          name=f"{floor} Till {t + 1}",
                                          code=f"{code}-T{n + 1}{t + 1}"))
    db.commit()


def _suppliers(db):
    out = []
    for name, gstin, state, sc, city, phone, email, bank in SUPPLIERS:
        s = models.Supplier(name=name, gstin=gstin, pan=gstin[2:12], state=state, state_code=sc,
                            address=f"{random.randint(1, 99)}, ABC Area, Sample City",
                            phone=phone, email=email, bank=bank)
        db.add(s)
        out.append(s)
    db.commit()
    return out


def _receipts(db, whs, sups):
    """Six months of receipts. The oldest are left untouched on purpose — they
    are what the dead-stock screen finds — and the last few stay as drafts, so
    "GRNs in draft" has something waiting."""
    silk_wh, cbe, tpr = whs["WH-C"], whs["WH-A"], whs["WH-B"]
    plan = []
    for idx, p in enumerate(PRODUCTS):
        is_silk = p[1] in ("LADIES-SAREE", "MENS-DHOTI SET", "PATTU PAVADAI") and p[8] in ("Pure Silk", "Silk", "Soft Silk")
        wh = silk_wh if is_silk else (cbe if idx % 3 else tpr)
        plan.append((wh, p))
        if not is_silk and idx % 4 == 0:          # restocked at the other garment warehouse
            plan.append((tpr if wh is cbe else cbe, p))
    # group lines into invoices: 2–4 products from one supplier per receipt
    by_sup = {}
    for wh, p in plan:
        by_sup.setdefault((p[9], wh.id), []).append((wh, p))
    groups = []
    for (_, _), items in by_sup.items():
        for i in range(0, len(items), 3):
            groups.append(items[i:i + 3])
    random.shuffle(groups)
    n = len(groups)
    for g, items in enumerate(groups):
        # spread from ~180 days ago to yesterday; the first three are the old, unmoved lots
        days_ago = 175 - g * 3 if g < 3 else max(1, int((n - g) * 120 / n))
        when = TODAY - dt.timedelta(days=days_ago, hours=random.randint(0, 6))
        wh, sup = items[0][0], sups[items[0][1][9]]
        purchase = models.Purchase(supplier_id=sup.id, warehouse_id=wh.id,
                                   grn_no=inv.next_grn_no(db, when),
                                   invoice_number=f"{sup.name.split()[0][:3].upper()}/{when:%y%m}/{100 + g}",
                                   invoice_date=when.strftime("%Y-%m-%d"), status="draft",
                                   created_at=when)
        db.add(purchase)
        db.flush()
        cat_id = inv.purchase_catalogue_id(db, purchase)
        for _, (desc, category, hsn, rate, mrp, brand, sizes, colours, material, _) in items:
            for size in sizes:
                for colour in colours[:1 + (g % 2)]:
                    full = f"{desc} {colour.upper()}"
                    qty = random.choice([12, 18, 24, 30, 36, 48]) if rate < 2000 else random.choice([4, 6, 8, 10])
                    match = inv.match_product(db, None, full, hsn, sup.id, catalogue_id=cat_id)
                    db.add(models.PurchaseLine(
                        purchase_id=purchase.id, description=full, qty=qty, uom="PCS", rate=rate,
                        amount=qty * rate, hsn=hsn, category=category, size=size, brand=brand,
                        design_no=f"{brand[:2].upper()}-{random.randint(1000, 9999)}",
                        mrp=mrp, sale_price=round(mrp * 0.9),
                        product_id=match.id if match else None, is_new_product=match is None))
        db.flush()
        # the bill's totals, as an invoice would print them: GST 5% on a piece up
        # to ₹1,000 and 12% above — without them a bill of ₹0 is never payable
        lines = db.query(models.PurchaseLine).filter(models.PurchaseLine.purchase_id == purchase.id).all()
        taxable = round(sum((l.qty or 0) * (l.rate or 0) for l in lines), 2)
        tax = round(sum((l.qty or 0) * (l.rate or 0) * (0.05 if (l.rate or 0) <= 1000 else 0.12) for l in lines), 2)
        purchase.taxable_total, purchase.tax_total = taxable, tax
        purchase.grand_total = round(taxable + tax)
        db.commit()
        db.refresh(purchase)
        if g >= n - 3:               # the newest three wait in draft
            continue
        res = inv.post_grn(db, purchase)
        if not res.get("ok"):
            db.rollback()
            print("skipped", purchase.invoice_number, res.get("error"))
            continue
        db.commit()
        db.query(models.StockMovement).filter(
            models.StockMovement.ref_type == "purchase",
            models.StockMovement.ref_id == purchase.id).update(
            {"created_at": when}, synchronize_session=False)
        purchase.posted_at = when
        db.commit()


def _transfers(db, whs):
    """Stock moving between warehouses: some received, some still on the road."""
    pairs = [("WH-A", "WH-B", 5, True), ("WH-B", "WH-A", 9, True),
             ("WH-A", "WH-B", 2, False), ("WH-B", "WH-A", 1, False)]
    for frm, to, days, receive in pairs:
        src, dst = whs[frm], whs[to]
        stocked = [p for p in db.query(models.Product).all()
                   if stock_loc.qty_at(db, p.id, src.id) >= 12][:3]
        if not stocked:
            continue
        when = TODAY - dt.timedelta(days=days)
        o = out_svc.create_outward(db, {
            "date": when.strftime("%Y-%m-%d"), "from_warehouse_id": src.id,
            "to_warehouse_id": dst.id, "packed_by": random.choice(["Sample Staff 01", "Sample Staff 02", "Sample Staff 03"]),
            "lines": [{"product_id": p.id, "qty": 6} for p in stocked]})
        db.commit()
        out_svc.post_outward(db, o)
        db.commit()
        if receive:
            out_svc.receive_outward(db, o, received_by=random.choice(["Sample Staff 04", "Sample Staff 05"]))
            db.commit()
        db.query(models.StockMovement).filter(
            models.StockMovement.ref_id == o.id,
            models.StockMovement.ref_type.in_(("outward", "transfer_in"))).update(
            {"created_at": when}, synchronize_session=False)
        db.commit()


def _detail_products(db):
    """Colour, size, fit and pattern on most products — "detailed" — and a few
    left without, so "Awaiting physical detail" has work in it."""
    fits = ["Regular", "Slim", "Relaxed"]
    patterns = ["Solid", "Printed", "Checked", "Striped", "Woven"]
    for i, p in enumerate(db.query(models.Product).order_by(models.Product.id).all()):
        words = p.description.split()
        p.color = p.color or words[-1].title()
        p.material = p.material or random.choice(["Cotton", "Silk", "Blend"])
        p.pattern = p.pattern or random.choice(patterns)
        p.fit = p.fit or random.choice(fits)
        if i % 6:
            p.detailed = True
            p.detailed_at = TODAY - dt.timedelta(days=random.randint(1, 30))
            p.detailed_by = random.choice(["Sample Staff 06", "Sample Staff 07"])
    db.commit()


if __name__ == "__main__":
    main()
