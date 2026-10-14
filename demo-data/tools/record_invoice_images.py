"""Record the sample invoices' scans into the static demo.

Every invoice screen shows the photographed page beside what was read off it
(GET /api/documents/<id>/image?page=0). The desktop recording kept the readings
but not the pages, so each sample invoice said "Page 1 is not in storage".

This asks the REAL backend — in process, over COPIES of the scratch build the
recording was made from (demo-build/demo.db and demo-build/uploads, the sample
scans the seed scripts drew: fictional suppliers, "Sample Company" as buyer) —
for each recorded document's pages, and adds them to recorded/manifest.json
with the image in recorded/bodies/.

    app\\backend\\.venv\\Scripts\\python.exe demo-data\\tools\\record_invoice_images.py
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
BUILD = DEMO / "demo-build"
RECORDED = DEMO / "recorded"
MANIFEST = RECORDED / "manifest.json"
BACKEND = Path(os.environ.get("WAREXX_BACKEND") or (DEMO.parent.parent / "app" / "backend"))

if not (BUILD / "demo.db").exists() or not (BUILD / "uploads").is_dir():
    sys.exit(f"{BUILD} needs demo.db and uploads/ — the scratch build the recording was made from")
tmp = Path(tempfile.mkdtemp(prefix="wx-scans-"))
shutil.copy(BUILD / "demo.db", tmp / "demo.db")
shutil.copytree(BUILD / "uploads", tmp / "uploads")
os.environ.update({
    "ESSA_DATABASE_URL": "sqlite:///" + str(tmp / "demo.db").replace("\\", "/"),
    "ESSA_WAREHOUSE_DB": str(tmp / "demo.db"),
    "ESSA_STATE_DIR": str(tmp),
    "ANTHROPIC_API_KEY": "",
})
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient                   # noqa: E402
from app import models                                      # noqa: E402
from app.database import SessionLocal                       # noqa: E402
from app.main import app                                    # noqa: E402
from app.services.users import mint_token                   # noqa: E402

db = SessionLocal()
admin = db.query(models.User).filter(models.User.username == "superadmin").first()
H = {"X-Essa-Token": mint_token(admin)}
client = TestClient(app)

manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
docs = {}
for e in manifest["entries"]:
    p = e.get("path", "")
    if e.get("method") == "GET" and p.startswith("/api/documents/") and p.count("/") == 3 and e.get("body"):
        try:
            d = json.loads(e["body"])["document"]
        except (ValueError, KeyError, TypeError):
            continue
        docs[d["id"]] = int(d.get("page_count") or 1)

new = []
(RECORDED / "bodies").mkdir(exist_ok=True)
for doc_id, pages in sorted(docs.items()):
    for page in range(pages):
        path = f"/api/documents/{doc_id}/image"
        r = client.get(f"{path}?page={page}", headers=H)
        kind = r.headers.get("content-type", "").split(";")[0]
        if r.status_code != 200 or not kind.startswith("image/"):
            print(f"  ! document {doc_id} page {page}: {r.status_code} {kind}")
            continue
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(kind, ".img")
        name = hashlib.sha1(r.content).hexdigest()[:16] + ext
        (RECORDED / "bodies" / name).write_bytes(r.content)
        new.append({"key": f"GET - {path}?page={page}", "method": "GET", "wh": "-", "path": path,
                    "search": f"?page={page}", "status": 200, "type": kind, "file": name})
        print(f"  document {doc_id} page {page}: {kind}, {len(r.content) // 1024} KB")

keys = {e["key"] for e in new}
manifest["entries"] = [e for e in manifest["entries"] if e["key"] not in keys] + new
MANIFEST.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
db.close()
shutil.rmtree(tmp, ignore_errors=True)
print(f"[record_invoice_images] {len(new)} pages recorded")
