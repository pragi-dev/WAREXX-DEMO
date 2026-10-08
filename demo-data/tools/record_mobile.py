"""Record what the phone app (/m) needs into the static demo.

The live demo's /m is the real phone app (backend/app/mobile) with sw.js standing
in for the server, exactly as the desktop demo is the real desktop app. The
desktop tour (record.mjs) never opened the phone's screens, so their answers are
recorded here — off the REAL backend, running against COPIES of the same sample
databases the desktop demo was recorded from:

  * every list and record the phone's screens read (GRNs, consignments, orders,
    transfers, cartons, products), in each sample warehouse;
  * the Command Centre (/api/assist/overview);
  * Ask WAREXX: a set of spoken/typed questions, their follow-ups ("only in
    warehouse b", "open the first one") and every tap an answer offers, each
    stored under a key sw.js can compute from the request (see KEYS below).

"Today" is pinned to the day the desktop demo was recorded (manifest meta), so
"today's GRNs" on the phone and the dashboard on the desktop describe the same
day of the same business. Entries the desktop recording already has are left as
they are.

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\record_mobile.py

KEYS (must match sw.js → assistKey)
    text:   POST <wh> /api/assist/command#T:<previous intent or ->:<norm(text)>
    tap:    POST <wh> /api/assist/command#I:<intent>|<k=v;…>|<all 0/1>
"""
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
ROOT = DEMO.parent.parent
BUILD = DEMO / "demo-build"
MANIFEST = DEMO / "recorded" / "manifest.json"
CMD = "/api/assist/command"

RULES = [(re.compile(r"\bESSA-(?=[A-Z0-9])"), "SMPL-"), (re.compile(r"\bESSA (?=[A-Z])"), "HOUSE "),
         (re.compile(r"\bTAQUA-(\d)"), r"DSN-\1"), (re.compile(r"\bTAQUA\b"), "HOUSE"),
         (re.compile(r"Taqua Casual"), "Sample Casual"), (re.compile(r"Essa Show Room"), "Sample Showroom")]


def scrub(text):
    for rx, to in RULES:
        text = rx.sub(to, text)
    return text


def norm(text):
    """The words of a question, as sw.js normalises them (keep in step)."""
    t = str(text or "").lower().replace("’", "").replace("'", "")
    t = re.sub(r"[^a-z0-9\- ]+", " ", t)
    t = " ".join(t.split())
    t = re.sub(r"^(?:(?:hey|ok|okay) )?warexx ", "", t)
    t = re.sub(r"^please ", "", t)
    t = re.sub(r" please$", "", t)
    t = re.sub(r"^show me ", "show ", t)
    return t


def canon(intent):
    f = intent.get("filters") or {}
    parts = sorted(f"{k}={v}" for k, v in f.items() if v not in (None, ""))
    return f"{intent.get('intent')}|{';'.join(parts)}|{1 if intent.get('all') else 0}"


# --- the sample business, on copies --------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="wx-mobile-"))
(tmp / "demo-build").mkdir()
for name in ("demo.db", "shop.db"):
    if (BUILD / name).exists():
        shutil.copy(BUILD / name, tmp / "demo-build" / name)
os.environ["ESSA_DATABASE_URL"] = "sqlite:///" + str(tmp / "demo-build" / "demo.db").replace("\\", "/")
os.environ["ESSA_WAREHOUSE_DB"] = str(tmp / "demo-build" / "demo.db")
os.environ["DATABASE_URL"] = "sqlite:///" + str(tmp / "demo-build" / "shop.db").replace("\\", "/")
os.environ["ESSA_STATE_DIR"] = str(tmp)
os.environ["ANTHROPIC_API_KEY"] = ""
if (tmp / "demo-build" / "shop.db").exists():
    os.environ["ESSA_POS_DB"] = str(tmp / "demo-build" / "shop.db")
sys.path.insert(0, str(ROOT / "app" / "backend"))

manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
recorded_at = dt.datetime.fromisoformat(manifest["meta"]["recordedAt"].replace("Z", ""))

from app.services import business_day                     # noqa: E402
FIXED_NOW = recorded_at + business_day.OFFSET
business_day.now_local = lambda: FIXED_NOW                  # "today" = the recording day

from fastapi.testclient import TestClient                  # noqa: E402
from app import models                                     # noqa: E402
from app.database import SessionLocal                      # noqa: E402
from app.main import app                                   # noqa: E402
from app.services.users import mint_token                  # noqa: E402

db = SessionLocal()
admin = db.query(models.User).filter(models.User.username == "superadmin").first()
TOKEN = mint_token(admin)
client = TestClient(app)
WHS = [w.id for w in db.query(models.Warehouse).order_by(models.Warehouse.id)]

existing = {e["key"] for e in manifest["entries"]}
new = {}


def put(key, method, wh, path, search, status, body):
    if key in existing:            # the desktop's own recording stays as it was
        return
    new[key] = {"key": key, "method": method, "wh": wh, "path": path, "search": search,
                "status": status, "type": "application/json", "body": body}


def get(path, wh):
    h = {"X-Essa-Token": TOKEN}
    if wh != "-":
        h["X-Essa-Warehouse"] = str(wh)
    r = client.get(path, headers=h)
    if r.status_code >= 500:
        print("  !", r.status_code, path)
        return None
    shown = scrub(path)
    p, _, q = shown.partition("?")
    put(f"GET {wh} {shown}", "GET", str(wh), p, ("?" + q) if q else "", r.status_code,
        scrub(r.text))
    try:
        return r.json()
    except ValueError:
        return None


def ask(wh, payload, ckey):
    r = client.post(CMD, json=payload, headers={"X-Essa-Token": TOKEN, "X-Essa-Warehouse": str(wh)})
    if r.status_code != 200:
        print("  !", r.status_code, ckey)
        return None
    out = r.json()
    put(f"POST {wh} {CMD}#{ckey}", "POST", str(wh), CMD, "#" + ckey, 200, scrub(r.text))
    return out


def main():
    P, PO, O, E, B, Pr = (models.Purchase, models.PurchaseOrder, models.StockOutward, models.LREntry,
                          models.Bundle, models.Product)
    for wh in WHS:
        print(f"warehouse {wh}")
        for path in ["/api/assist/overview", "/api/stock-audit/current", "/api/bundles/locations",
                     "/api/notifications", "/api/notifications/count", "/api/masters/categories",
                     "/api/purchases/shortage-options", "/api/inventory/product-options",
                     "/api/lr/receivers", "/api/bundles", "/api/inventory/products?status=all"]:
            get(path, wh)
        for f in ("draft", "posted", "all"):
            get(f"/api/purchases/page?filter={f}&size=100&page=1", wh)
        for f in ("pending", "received", "all"):
            get(f"/api/lr/page?received={f}&size=100&page=1", wh)
        for f in ("stored", "opened", "tagged"):
            get(f"/api/bundles?status={f}", wh)
        for st in ("pending", "all", "detailed"):
            get(f"/api/inventory/products?status={st}&q=&limit=101&offset=0", wh)
        for st in ("all", "pending", "draft", "confirmed", "cancelled"):
            get(f"/api/purchase-orders?status={st}", wh)
        for st in ("posted", "received", "draft"):
            get(f"/api/outward?status={st}&kind=all", wh)
        for (i,) in db.query(P.id):
            get(f"/api/purchases/{i}", wh)
        for (i,) in db.query(PO.id):
            get(f"/api/purchase-orders/{i}", wh)
        for (i,) in db.query(O.id):
            get(f"/api/outward/{i}", wh)
        for (i,) in db.query(E.id):
            get(f"/api/lr/{i}", wh)
        for (i,) in db.query(B.id):
            get(f"/api/bundles/{i}", wh)
        for (i,) in db.query(Pr.id):
            get(f"/api/inventory/products/{i}", wh)
    for (sku,) in db.query(Pr.sku).filter(Pr.sku.isnot(None)):
        get(f"/api/inventory/lookup?code={sku}", "-")

    # ---- Ask WAREXX ------------------------------------------------------
    for wh in WHS:
        bal = models.StockBalance
        skus = [s for (s,) in db.query(Pr.sku).join(bal, bal.product_id == Pr.id)
                .filter(bal.warehouse_id == wh, bal.qty > 0).order_by(bal.qty.desc()).limit(3)]
        inv = db.query(P.invoice_number).filter(P.warehouse_id == wh, P.invoice_number.isnot(None)).first()
        texts = [
            "show low stock items", "show low stock", "low stock", "which products are below minimum stock",
            "show low stock in warehouse a", "show low stock in warehouse b", "show low stock in warehouse c",
            "show pending grns", "pending grns", "show todays grns", "show grns posted today",
            "show pending purchase orders", "pending purchase orders", "show pending pos",
            "show overdue purchase orders", "show confirmed purchase orders", "show draft purchase orders",
            "where is product ABC123", "show stock for ABC123",
            "show todays inward stock", "show inward stock", "show todays outward stock", "show outward stock",
            "how many items were dispatched today", "show stock movement", "show stock movements",
            "show todays stock movements", "what needs my attention today", "what needs my attention",
            "whats pending today", "show my pending tasks", "find invoice INV-1024",
            "show inventory in warehouse a", "show inventory in warehouse b", "show inventory in warehouse c",
            "show stock in warehouse a", "how much stock do we have", "show recent activity",
            "show recent inventory activity", "recent activity", "show transfers to receive",
            "show incoming transfers", "show shortages", "show invoices to review",
            "show products awaiting detail", "show consignments to receive", "start a stock count",
            "create a stock transfer from warehouse a to warehouse b",
            "create a stock transfer from sample warehouse a to sample warehouse b",
            "receive this stock", "mark this item as damaged", "raise a stock request",
            "create a grn for this purchase order", "show todays sales", "what is todays profit",
            "asdfgh", "show something that doesnt exist",
        ]
        for s in skus:
            texts += [f"where is product {s}", f"show stock for {s}", f"find {s}", s,
                      f"show stock movement for {s}"]
        if inv:
            texts += [f"find invoice {inv[0]}"]
        ov = client.get("/api/assist/overview", headers={"X-Essa-Token": TOKEN, "X-Essa-Warehouse": str(wh)}).json()
        texts += ov.get("suggestions") or []
        seen_i, followed = set(), set()
        queue = [m["intent"] for m in ov.get("metrics", [])] + \
                [a["intent"] for a in ov.get("actions", []) if a.get("intent")]
        queue += [{"intent": i, "filters": {}, "all": False} for i in
                  ("MOVEMENTS", "LOW_STOCK", "OUTWARD", "INWARD", "PENDING_GRN", "SHORTAGES",
                   "INVOICE_REVIEW", "ACTIVITY", "PO_OVERDUE")]
        queue += [{"intent": "RECEIVE_TRANSFER", "action": "CREATE", "filters": {"transfer_id": o.id}}
                  for o in db.query(O).filter(O.status == "posted", O.to_warehouse_id == wh)]

        def offered(ans):
            """Every tap an answer offers: row buttons, card buttons, View all."""
            out = []
            for it in ans.get("items") or []:
                if (it.get("action") or {}).get("intent"):
                    out.append(it["action"]["intent"])
            for a in ans.get("actions") or []:
                if a.get("intent"):
                    out.append(a["intent"])
            if (ans.get("total") or 0) > len(ans.get("items") or []) and ans.get("kind") == "list":
                out.append({**(ans.get("intent") or {}), "all": True})
            return out

        def follow(ans, text_key_prev):
            prev = (ans.get("context") or {}).get("intent") or {}
            name = prev.get("intent")
            # once per kind of question: sw.js keys a follow-up on the previous
            # intent, not on its exact wording or filters
            if not name or name in followed or ans.get("kind") not in ("list", "product", "empty"):
                return
            followed.add(name)
            for t in ("only in warehouse a", "only in warehouse b", "only in warehouse c", "show all",
                      "open the first one", "open the second one", "open the third one",
                      "receive the first one", "receive it"):
                r = ask(wh, {"text": t, "context": ans.get("context")}, f"T:{name}:{norm(t)}")
                if r:
                    queue.extend(offered(r))

        for t in dict.fromkeys(texts):
            k = norm(scrub(t))
            ans = ask(wh, {"text": t, "context": None}, f"T:-:{k}")
            if ans:
                queue.extend(offered(ans))
                follow(ans, k)
        n = 0
        while queue and n < 400:
            it = queue.pop(0)
            k = canon(it)
            if k in seen_i:
                continue
            seen_i.add(k)
            n += 1
            ans = ask(wh, {"intent": it, "context": None}, "I:" + scrub(k))
            if ans:
                queue.extend(offered(ans))
        print(f"  {len(dict.fromkeys(texts))} questions, {n} taps")

    manifest["entries"] = manifest["entries"] + list(new.values())
    manifest.setdefault("meta", {})["mobile"] = {
        "recordedAt": dt.datetime.utcnow().isoformat() + "Z", "pinnedDay": FIXED_NOW.date().isoformat(),
        "entries": len(new)}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"recorded {len(new)} new answer(s) for the phone")


try:
    main()
finally:
    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
