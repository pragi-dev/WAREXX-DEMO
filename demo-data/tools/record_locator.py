"""Record the Item Locator's answers into the static demo.

The demo replays answers recorded off a real server, and no locator lookup was
ever recorded — so every code, even a SKU listed on the demo's own Inventory
screen, came back "nothing found". This runs the real /api/inventory/locate
against COPIES of the sample business's databases and writes:

  * one answer per product (looked up by its SKU) and per carton label;
  * a compact index of every per-piece code -> its product and the piece's own
    number and status, which sw.js turns into a piece answer (2,540 full answers
    would be most of the demo's download for one screen).

Every recorded text goes through scrub.mjs's rules (ESSA- codes read SMPL- in the
demo), exactly as record.mjs does.

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\record_locator.py
"""
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

# the same rules as scrub.mjs, so the demo never shows the app's own names
RULES = [(re.compile(r"\bESSA-(?=[A-Z0-9])"), "SMPL-"), (re.compile(r"\bESSA (?=[A-Z])"), "HOUSE "),
         (re.compile(r"\bTAQUA-(\d)"), r"DSN-\1"), (re.compile(r"\bTAQUA\b"), "HOUSE"),
         (re.compile(r"Taqua Casual"), "Sample Casual"), (re.compile(r"Essa Show Room"), "Sample Showroom")]


def scrub(text):
    for rx, to in RULES:
        text = rx.sub(to, text)
    return text


tmp = Path(tempfile.mkdtemp(prefix="wx-locate-"))
(tmp / "demo-build").mkdir()
for name in ("demo.db", "shop.db"):
    if (BUILD / name).exists():
        shutil.copy(BUILD / name, tmp / "demo-build" / name)
os.environ["ESSA_DATABASE_URL"] = "sqlite:///" + str(tmp / "demo-build" / "demo.db").replace("\\", "/")
os.environ["ESSA_STATE_DIR"] = str(tmp)
if (tmp / "demo-build" / "shop.db").exists():
    os.environ["ESSA_POS_DB"] = str(tmp / "demo-build" / "shop.db")
sys.path.insert(0, str(ROOT / "app" / "backend"))

from fastapi import HTTPException                 # noqa: E402
from app import models                            # noqa: E402
from app.database import SessionLocal             # noqa: E402
from app.routers.inventory import locate          # noqa: E402

PATH = "/api/inventory/locate"


def entry(search, body, status=200):
    return {"key": f"GET - {PATH}{search}", "method": "GET", "wh": "-", "path": PATH,
            "search": search, "status": status, "type": "application/json", "body": body}


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [e for e in manifest["entries"] if e.get("path") not in (PATH, PATH + "/units")]
    db = SessionLocal()
    n = 0
    try:
        codes = [p.sku for p in db.query(models.Product).order_by(models.Product.id) if p.sku]
        codes += [b.code for b in db.query(models.Bundle).order_by(models.Bundle.id) if b.code]
        for code in codes:
            try:
                out = locate(code=code, product_id=0, db=db)
            except HTTPException:
                continue
            shown = scrub(code)
            entries.append(entry("?code=" + shown, scrub(json.dumps(out, ensure_ascii=False, default=str))))
            n += 1
        units = {}
        for u in db.query(models.ProductUnit):
            if u.code and u.product and u.product.sku:
                units[scrub(u.code).upper()] = {"sku": scrub(u.product.sku), "seq": u.seq,
                                                "status": getattr(u, "status", None)}
        entries.append(entry("", json.dumps(units, ensure_ascii=False)) | {
            "key": f"GET - {PATH}/units", "path": PATH + "/units"})
    finally:
        db.close()
    manifest["entries"] = entries
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"recorded {n} locator answer(s) and {len(units)} piece code(s)")


if __name__ == "__main__":
    main()
