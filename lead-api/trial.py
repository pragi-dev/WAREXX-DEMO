"""Demo trials: a visitor picks a time slot on the landing page, signs up with
their email and a password, and can use the interactive demo for two hours from
the start of that slot. After that the password no longer works.

There is no database. Everything is a signed pass the visitor's browser keeps:

    ticket   issued with the lead: email + the window. Lets the visitor sign up.
    account  issued at sign-up: the ticket's email and window + a salted
             PBKDF2 hash of the password. Signing in needs it AND the password.
    session  issued at sign-in, only inside the window: email + window end.

Each pass is base64url(JSON) + "." + base64url(HMAC-SHA256), signed with
DEMO_TRIAL_SECRET, so a visitor cannot move their own window or forge a pass.
The window is always checked here, on the server, against the server's clock.

What this is: the gate in front of a demo of fictional sample data. It does not
protect anything secret; it decides who sees the demo, and for how long.
A pass lives in one browser: a visitor on another device books again.

Settings (server-side environment, never VITE_*):
    DEMO_TRIAL_SECRET    REQUIRED on a deployment: a long random value. Without
                         it trial requests answer 503 (local server.py makes a
                         temporary one per run).
    DEMO_TRIAL_MINUTES   window length, default 120
    DEMO_SLOT_HOURS      slot start hours, default "10,12,15,17"
    DEMO_SLOT_TZ_MINUTES UTC offset of those hours in minutes, default 330 (IST)
    DEMO_SLOT_DAYS       how far ahead a slot may be booked, default 7
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import time

WINDOW = int(os.environ.get("DEMO_TRIAL_MINUTES") or 120) * 60
SLOT_HOURS = sorted({int(h) for h in (os.environ.get("DEMO_SLOT_HOURS") or "10,12,15,17").split(",") if h.strip()})
TZ = dt.timezone(dt.timedelta(minutes=int(os.environ.get("DEMO_SLOT_TZ_MINUTES") or 330)))
DAYS = int(os.environ.get("DEMO_SLOT_DAYS") or 7)
PBKDF2_ROUNDS = 120_000
MIN_PASSWORD = 8


class TrialError(Exception):
    def __init__(self, status, detail, **extra):
        super().__init__(detail)
        self.status, self.detail, self.extra = status, detail, extra


def _secret():
    s = (os.environ.get("DEMO_TRIAL_SECRET") or "").strip()
    if len(s) < 16:
        raise TrialError(503, "trial_not_configured")
    return s.encode()


def configured():
    try:
        _secret()
        return True
    except TrialError:
        return False


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(payload):
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return body + "." + mac


def unsign(token, kind):
    try:
        body, mac = str(token).split(".", 1)
        good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, good):
            raise ValueError
        payload = json.loads(_unb64(body))
    except TrialError:
        raise
    except Exception:  # noqa: BLE001 — any malformed or altered pass is the same answer
        raise TrialError(401, "invalid_pass")
    if payload.get("k") != kind:
        raise TrialError(401, "invalid_pass")
    return payload


# ---- slots ---------------------------------------------------------------

def slot_window(slot, now=None):
    """(start, end) epoch seconds for a slot: "now", or the ISO start time of
    one of the daily slots within the next DAYS days. Anything else is refused,
    so a visitor cannot pick their own hours."""
    now = time.time() if now is None else now
    if not slot or slot == "now":
        start = int(now)
        return start, start + WINDOW
    try:
        when = dt.datetime.fromisoformat(str(slot).replace("Z", "+00:00"))
    except ValueError:
        raise TrialError(422, "invalid_slot")
    if when.tzinfo is None:
        raise TrialError(422, "invalid_slot")
    local = when.astimezone(TZ)
    start = int(when.timestamp())
    if (local.hour not in SLOT_HOURS or local.minute or local.second
            or start + WINDOW <= now or start > now + DAYS * 86400):
        raise TrialError(422, "invalid_slot")
    return start, start + WINDOW


def describe(start, end):
    a, b = dt.datetime.fromtimestamp(start, TZ), dt.datetime.fromtimestamp(end, TZ)
    off = TZ.utcoffset(None)
    tz = "IST" if off == dt.timedelta(minutes=330) else "UTC%+03d:%02d" % divmod(int(off.total_seconds() // 60), 60)
    return f"{a:%a %d %b}, {a:%I:%M %p} – {b:%I:%M %p} {tz}".replace(" 0", " ")


# ---- passes --------------------------------------------------------------

def ticket(email, slot, now=None):
    start, end = slot_window(slot, now)
    return {"ticket": sign({"k": "t", "e": email.lower(), "s": start, "x": end, "n": secrets.token_hex(6)}),
            "email": email.lower(), "start": start, "end": end, "window": describe(start, end)}


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()


def _window_check(p, now):
    if now >= p["x"]:
        raise TrialError(403, "expired", end=p["x"])
    if now < p["s"]:
        raise TrialError(403, "not_yet", start=p["s"], end=p["x"], window=describe(p["s"], p["x"]))


def _session(p):
    return {"session": sign({"k": "s", "e": p["e"], "s": p["s"], "x": p["x"]}),
            "email": p["e"], "start": p["s"], "end": p["x"], "window": describe(p["s"], p["x"])}


def signup(ticket_token, password, now=None):
    now = time.time() if now is None else now
    t = unsign(ticket_token, "t")
    if now >= t["x"]:
        raise TrialError(403, "expired", end=t["x"])
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise TrialError(422, "weak_password")
    salt = secrets.token_hex(16)
    acc = {"k": "a", "e": t["e"], "s": t["s"], "x": t["x"], "salt": salt, "h": _hash(password, salt)}
    # a session straight away, even before the slot: the demo then waits for the
    # slot to open (verify answers not_yet) without asking for the password again
    return {"account": sign(acc), **_session(acc)}


def login(account_token, email, password, now=None):
    now = time.time() if now is None else now
    a = unsign(account_token, "a")
    if (str(email or "").strip().lower() != a["e"]
            or not hmac.compare_digest(_hash(str(password or ""), a["salt"]), a["h"])):
        raise TrialError(401, "wrong_password")
    if now >= a["x"]:
        raise TrialError(403, "expired", end=a["x"])
    return _session(a)


def verify(session_token, now=None):
    now = time.time() if now is None else now
    s = unsign(session_token, "s")
    _window_check(s, now)
    return {"ok": True, "email": s["e"], "end": s["x"], "now": int(now)}


def handle(raw):
    """POST /api/trial — {"action": "signup"|"login"|"verify", ...}."""
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise TrialError(400, "bad_request")
    action = data.get("action")
    if action == "status":             # is the sign-up set up on this deployment?
        return {"configured": configured()}
    if action == "signup":
        return signup(data.get("ticket"), data.get("password"))
    if action == "login":
        return login(data.get("account"), data.get("email"), data.get("password"))
    if action == "verify":
        return verify(data.get("session"))
    raise TrialError(400, "bad_request")
