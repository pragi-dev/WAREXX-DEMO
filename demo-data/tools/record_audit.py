"""Record the Audit Trail's answers into the static demo.

The demo (landing-demo/frontend/demo) replays answers recorded off a real server, and the
audit trail was never among them — so the screen got the demo's "empty list"
answer and had nothing to show. This runs the real /api/audit endpoint against a
COPY of the sample business's database and writes its answers (every event, and
each outcome filter) into demo-data/recorded/manifest.json.

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\record_audit.py

The copy is made in a temporary folder; demo-build/demo.db is only read.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
ROOT = DEMO.parent.parent
SRC_DB = DEMO / "demo-build" / "demo.db"
MANIFEST = DEMO / "recorded" / "manifest.json"

tmp = Path(tempfile.mkdtemp(prefix="wx-audit-"))
db_copy = tmp / "demo-build" / "demo.db"
db_copy.parent.mkdir(parents=True)
shutil.copy(SRC_DB, db_copy)
os.environ["ESSA_DATABASE_URL"] = "sqlite:///" + str(db_copy).replace("\\", "/")
os.environ.setdefault("ESSA_STATE_DIR", str(tmp))
sys.path.insert(0, str(ROOT / "app" / "backend"))

from app.database import SessionLocal          # noqa: E402
from app.routers.audit import events            # noqa: E402

LIMIT = 100
# What the screen asks for: every outcome, then each of its three chips.
VIEWS = ["", "ok", "refused", "failed"]


def main():
    request = SimpleNamespace(state=SimpleNamespace(user={"role": "superadmin", "username": "superadmin"},
                                                    warehouses=None))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [e for e in manifest["entries"] if e.get("path") != "/api/audit"]
    db = SessionLocal()
    try:
        for outcome in VIEWS:
            out = events(request, limit=LIMIT, before=None, user=None, screen=None, warehouse_id=None,
                         date_from=None, date_to=None, q=None, outcome=outcome or None, db=db)
            search = f"?limit={LIMIT}" + (f"&outcome={outcome}" if outcome else "")
            entries.append({"key": f"GET - /api/audit{search}", "method": "GET", "wh": "-",
                            "path": "/api/audit", "search": search, "status": 200,
                            "type": "application/json",
                            "body": json.dumps(out, ensure_ascii=False, default=str)})
            print(f"  /api/audit{search}: {len(out.get('events') or [])} event(s)")
    finally:
        db.close()
    manifest["entries"] = entries
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
