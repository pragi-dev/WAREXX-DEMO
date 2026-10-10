"""POST /api/admin — the admin page's data: leads and trial users from store.py.

    {"action": "login", "password": "…"}          → {"token": …}  (12 hours)
    {"action": "summary" | "leads" | "trials" | "export", "token": …, …}

Locked by one password, ADMIN_PASSWORD (a server-side environment variable —
never VITE_*, never in a page). Without it the admin is switched off entirely.
The token is signed with a key made from that password, so changing the password
signs everyone out. Sign-in attempts are rate-limited per address.

What it shows is visitors' contact details: the page that reads it is not linked
from anywhere, is marked noindex, and every answer is no-store.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import threading
import time
from collections import defaultdict, deque

import store

TOKEN_HOURS = 12
PAGE = 50
LOGIN_PER_15_MIN = 10
_attempts = defaultdict(deque)
_lock = threading.Lock()


class AdminError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _password():
    return (os.environ.get("ADMIN_PASSWORD") or "").strip()


def _key():
    pw = _password()
    if len(pw) < 8:
        raise AdminError(503, "admin_not_configured")
    return hashlib.sha256(("warexx-admin:" + pw + ":" + (os.environ.get("DEMO_TRIAL_SECRET") or "")).encode()).digest()


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _token(now=None):
    now = time.time() if now is None else now
    body = _b64(json.dumps({"k": "adm", "x": int(now) + TOKEN_HOURS * 3600}).encode())
    return body + "." + _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def _check(token, now=None):
    now = time.time() if now is None else now
    try:
        body, mac = str(token).split(".", 1)
        if not hmac.compare_digest(mac, _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())):
            raise ValueError
        p = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except AdminError:
        raise
    except Exception:  # noqa: BLE001
        raise AdminError(401, "signed_out")
    if p.get("k") != "adm" or now >= p.get("x", 0):
        raise AdminError(401, "signed_out")


def _allowed(ip):
    now = time.time()
    with _lock:
        q = _attempts[ip]
        while q and now - q[0] > 900:
            q.popleft()
        if len(q) >= LOGIN_PER_15_MIN:
            return False
        q.append(now)
        return True


def _cell(v):
    """A spreadsheet must not run a visitor's text as a formula."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _csv(cols, rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in cols])
    return out.getvalue()


def handle(raw, ip="", token=""):
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise AdminError(400, "bad_request")
    if not isinstance(data, dict):
        raise AdminError(400, "bad_request")
    action = data.get("action")
    if action == "login":
        _key()                                         # 503 when not configured
        if not _allowed(ip or "?"):
            raise AdminError(429, "too_many_attempts")
        if not hmac.compare_digest(str(data.get("password") or "").encode(), _password().encode()):
            raise AdminError(401, "wrong_password")
        return {"token": _token(), "hours": TOKEN_HOURS}
    _check(data.get("token") or token)
    page = max(0, int(data.get("page") or 0))
    q = str(data.get("q") or "").strip()[:100]
    if action == "summary":
        return store.summary()
    if action == "leads":
        return {**store.leads(q, str(data.get("type") or ""), PAGE, page * PAGE), "page": page, "per_page": PAGE}
    if action == "trials":
        return {**store.trials(q, str(data.get("status") or ""), PAGE, page * PAGE), "page": page, "per_page": PAGE}
    if action == "export":
        what = data.get("what")
        if what == "leads":
            return {"filename": "warexx-leads.csv", "csv": _csv(store.LEAD_COLS, store.leads(q, str(data.get("type") or ""), 100000, 0)["rows"])}
        if what == "trials":
            return {"filename": "warexx-trial-users.csv", "csv": _csv(store.TRIAL_COLS, store.trials(q, str(data.get("status") or ""), 100000, 0)["rows"])}
    raise AdminError(400, "bad_request")
