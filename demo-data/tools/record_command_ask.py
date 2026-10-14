"""Record the Command Center's "Ask WAREXX" into the static demo.

The Command Center's question box posts {"q": …} to /api/command/ask, and a
code it recognises (a SKU, a GRN, a bill) is followed with
GET /api/command/trace?code=…&kind=…&ref=…. The desktop recording predates the
screen, so the demo answered every question with its generic "saved" reply and
the answer panel stayed empty.

This asks the REAL backend — in process, over COPIES of the scratch databases
the desktop demo was recorded from (demo-build/demo.db + shop.db, the till
included) — with "today" pinned to the recording's last day, so its answers
agree with the Command Center tiles (record_command.mjs) and the rest of the
demo. It asks:

  * every example question the screen offers (ask_anything.examples());
  * a set of everyday phrasings of the same questions;
  * what clicking a row asks: each product's SKU, floor and store the recorded
    overviews list;
  * and, for an answer that offers choices (a code that matched several
    records), each choice's trace.

    app\\backend\\.venv\\Scripts\\python.exe demo-data\\tools\\record_command_ask.py

KEYS (must match sw.js → commandAnswer / commandTrace)
    question:  POST - /api/command/ask#Q:<norm(q)>
    trace:     GET - /api/command/trace?code=…[&kind=…&ref=…]   (as api.js's qs() writes it)
"""
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
BUILD = DEMO / "demo-build"
MANIFEST = DEMO / "recorded" / "manifest.json"
BACKEND = Path(os.environ.get("WAREXX_BACKEND") or (DEMO.parent.parent / "app" / "backend"))
ASK, TRACE = "/api/command/ask", "/api/command/trace"

RULES = [(re.compile(r"\bESSA-(?=[A-Z0-9])"), "SMPL-"), (re.compile(r"\bESSA (?=[A-Z])"), "HOUSE "),
         (re.compile(r"\bTAQUA-(\d)"), r"DSN-\1"), (re.compile(r"\bTAQUA\b"), "HOUSE"),
         (re.compile(r"Taqua Casual"), "Sample Casual"), (re.compile(r"Essa Show Room"), "Sample Showroom")]


def scrub(text):
    for rx, to in RULES:
        text = rx.sub(to, text)
    return text


def norm(text):
    """The words of a question, as sw.js normalises them (keep in step: sw.js
    cmdNorm). Letters of any script are kept, with their vowel signs (Tamil is
    written with them), so a question spoken in Tamil has words too."""
    t = str(text or "").lower().replace("’", "").replace("'", "")
    t = "".join(ch if unicodedata.category(ch)[0] in "LMN" or ch in "- " else " " for ch in t)
    t = " ".join(t.split())
    t = re.sub(r"^(?:(?:hey|ok|okay) )?warexx ", "", t)
    t = re.sub(r"^please ", "", t)
    t = re.sub(r" please$", "", t)
    t = re.sub(r"^show me ", "show ", t)
    return t


# --- the sample business, on copies, "today" pinned ----------------------------
if not (BUILD / "demo.db").exists() or not (BUILD / "shop.db").exists():
    sys.exit(f"{BUILD} needs demo.db and shop.db — the scratch databases the recording was made from")
tmp = Path(tempfile.mkdtemp(prefix="wx-cmdask-"))
for name in ("demo.db", "shop.db"):
    shutil.copy(BUILD / name, tmp / name)
os.environ.update({
    "ESSA_DATABASE_URL": "sqlite:///" + str(tmp / "demo.db").replace("\\", "/"),
    "ESSA_WAREHOUSE_DB": str(tmp / "demo.db"),
    "DATABASE_URL": "sqlite:///" + str(tmp / "shop.db").replace("\\", "/"),
    "ESSA_POS_DB": str(tmp / "shop.db"),
    "ESSA_STATE_DIR": str(tmp),
    "ANTHROPIC_API_KEY": "",                       # keyword answers only: nothing is billed, nothing varies
})
sys.path.insert(0, str(BACKEND))
manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
recorded_at = dt.datetime.fromisoformat(manifest["meta"]["recordedAt"].replace("Z", ""))

from app.services import business_day                      # noqa: E402
FIXED_NOW = recorded_at + business_day.OFFSET
business_day.now_local = lambda: FIXED_NOW                   # "today" = the recording day

from fastapi.testclient import TestClient                   # noqa: E402
from app import models                                      # noqa: E402
from app.database import SessionLocal                       # noqa: E402
from app.main import app                                    # noqa: E402
from app.services import ask_anything                       # noqa: E402
from app.services.users import mint_token                   # noqa: E402

db = SessionLocal()
admin = db.query(models.User).filter(models.User.username == "superadmin").first()
H = {"X-Essa-Token": mint_token(admin)}
client = TestClient(app)
new = {}


def put(key, method, path, search, status, body):
    new[key] = {"key": key, "method": method, "wh": "-", "path": path, "search": search,
                "status": status, "type": "application/json", "body": body}


def real(text):
    """The demo shows codes scrubbed (ESSA-… → SMPL-…); the databases hold the real ones."""
    return re.sub(r"(?<![A-Za-z0-9])SMPL-(?=[A-Z0-9])", "ESSA-", str(text))


def ask(q, expect=None):
    t = norm(q)
    if not t or f"POST - {ASK}#Q:{t}" in new:
        return None
    r = client.post(ASK, json={"q": real(q)}, headers=H)
    if r.status_code >= 500:
        print("  !", r.status_code, q)
        return None
    j = r.json()
    title = str(j.get("title") or "")
    if expect and (j.get("ok") is False or not (title == expect or title.startswith(expect + " ")
                                                 or title.startswith(expect + "—") or title.startswith(expect))):
        print(f"  {q!r}: answered as {title!r}, not {expect!r} — left to the demo's suggestions")
        return None
    if expect == "Sales" and not title.startswith("Sales"):
        return None
    put(f"POST - {ASK}#Q:{t}", "POST", ASK, "", r.status_code, scrub(r.text))
    print(f"  {q!r}: {j.get('title')} — {str(j.get('headline') or j.get('line') or '')[:70]}")
    # a code that matched several records: each one, as the box asks for it (api.js qs())
    for c in j.get("choices") or []:
        trace(j.get("code") or q, c.get("kind"), c.get("id"))
    return j


def trace(code, kind=None, ref=None):
    params = {k: v for k, v in (("code", code), ("kind", kind), ("ref", ref)) if v not in (None, "")}
    search = "?" + urlencode(params)
    key = f"GET - {TRACE}{scrub(search)}"
    if key in new:
        return
    r = client.get(TRACE + "?" + urlencode({k: real(v) for k, v in params.items()}), headers=H)
    if r.status_code < 500:
        put(key, "GET", TRACE, scrub(search), r.status_code, scrub(r.text))


# 1. the screen's own examples, and everyday ways of asking the same
questions = [e["q"] for e in ask_anything.examples()]
questions += [
    "today's sales", "total sales today", "sales today", "what are today's sales", "how much did we sell today",
    "today's profit", "profit today", "what is today's profit", "today's discounts", "discounts given today",
    "today's invoices", "today's GRNs", "goods received today", "today's LR entries",
    "sales this week", "sales this month", "sales yesterday", "top selling products", "best selling products",
    "which store has the highest sales?", "which warehouse has the highest sales?", "sales by store",
    "low stock", "low stock items", "which products need reordering?", "dead stock", "stock value",
    "total stock value", "stock by warehouse", "pending supplier payments", "pending GRNs",
    "who gave the highest discount?", "what did people do today", "returns today",
]
# 2. what clicking a row on the Command Center asks: a product's SKU, a floor, a store
seen = set()
for e in manifest["entries"]:
    if e.get("path") != "/api/command/overview" or not e.get("body"):
        continue
    ov = json.loads(e["body"])
    for r in ov.get("top_products") or []:
        seen.add(r.get("sku") or r.get("label"))
    for r in ov.get("top_floors") or []:      # the Leaders list: products and floors
        seen.add(r.get("label"))
    # (stores, tills and people are not clickable questions on this screen)
questions += sorted(s for s in seen if s)

# 3. spoken in Tamil (the box listens for Tamil too): kept only where the server answers it
# (question, the answer it must be): the server's keyword reading of Tamil is
# coarse, so an answer is kept only when it is the one the question asks for —
# a wrong figure in a demo is worse than "try one of these"
TAMIL = [
    ("இன்றைய மொத்த விற்பனை", "Today's Sales"), ("இன்றைய விற்பனை", "Today's Sales"),
    ("இன்று விற்பனை எவ்வளவு", "Today's Sales"), ("இன்றைய மொத்த விற்பனை எவ்வளவு", "Today's Sales"),
    ("இன்றைய லாபம்", "Today's Profit"), ("இன்று லாபம் எவ்வளவு", "Today's Profit"),
    ("இன்றைய தள்ளுபடி", "Today's Discounts"), ("இன்று கொடுத்த தள்ளுபடி", "Today's Discounts"),
    ("இன்றைய இன்வாய்ஸ்", "Today's Invoice"), ("இன்றைய மொத்த இன்வாய்ஸ்", "Today's Invoice"),
    ("இன்றைய ஜிஆர்என்", "Today's GRN"), ("இன்றைய எல்ஆர்", "Today's LR"),
    ("குறைந்த இருப்பு பொருட்கள்", "Low Stock"), ("குறைவான இருப்பு", "Low Stock"), ("இருப்பு குறைவான பொருட்கள்", "Low Stock"),
    ("விற்பனை இல்லாத பொருட்கள்", "Dead Stock"), ("90 நாட்களாக விற்பனை இல்லாத பொருட்கள்", "Dead Stock"),
    ("எந்த தளத்தில் அதிக விற்பனை", "Highest Sales by Floor"), ("எந்த கடையில் அதிக விற்பனை", "Highest Sales by Store"),
    ("எந்த கிடங்கில் அதிக இருப்பு மதிப்பு", "Highest Stock Value"),
    ("நிலுவையில் உள்ள சப்ளையர் பணம்", "Pending Supplier Payments"), ("சப்ளையர் நிலுவை தொகை", "Pending Supplier Payments"),
    ("இன்றைய ரிட்டர்ன்", "Today's Returns"), ("நேற்றைய விற்பனை", "Yesterday's Sales"),
    ("இந்த மாத விற்பனை", "Sales"), ("அதிகம் விற்பனையான பொருட்கள்", "Highest Sales by Product"),
    ("இருப்பு மதிப்பு", "Stock Available"), ("கீழ் தளத்தில் எவ்வளவு இருப்பு உள்ளது", "Stock on Ground Floor"),
    ("இன்று என்ன நடந்தது", "Today's Activity"),
]

print(f"[record_command_ask] today = {FIXED_NOW.date()}; {len(questions)} questions + {len(TAMIL)} in Tamil")
for q in questions:
    ask(q)
for q, must in TAMIL:
    ask(q, expect=must)

before = len(manifest["entries"])
keep = [e for e in manifest["entries"] if e.get("path") not in (ASK, TRACE)]
manifest["entries"] = keep + list(new.values())
manifest["meta"]["commandAsk"] = {"recordedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  "pinnedDay": FIXED_NOW.date().isoformat(), "entries": len(new)}
MANIFEST.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
db.close()
shutil.rmtree(tmp, ignore_errors=True)
print(f"[record_command_ask] {len(new)} answers recorded ({before - len(keep)} replaced)")
