"""The lead API's HTTP handling, shared by the local server (server.py) and the
Vercel functions (../api/leads.py, ../api/trial.py). Two endpoints:

    POST /api/leads   a landing page form. With a demo slot it also answers a
                      sign-up pass for the demo (trial.py).
    POST /api/trial   the demo's sign-up / sign-in / session check (trial.py).

Nothing here can reach WAREXX: no database driver is imported, no WAREXX URL or
credential is read. GET, PUT, PATCH and DELETE are refused.
"""
import json
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler

import lead_mail
import trial

#: Sign-up / sign-in attempts per address per hour (a password guess costs one).
TRIAL_PER_HOUR = 30
_trial_recent = defaultdict(deque)
_trial_lock = threading.Lock()


#: A free-trial request answered in the last few minutes, by (email, slot): a
#: double-click or a retry gets the same trial back instead of a second pass
#: and a second email — and the email again only if the first one failed.
BOOKING_TTL = 10 * 60
_bookings = {}
_bookings_lock = threading.Lock()


def _booking(key, value=None):
    now = time.time()
    with _bookings_lock:
        for k in [k for k, (t, _) in _bookings.items() if now - t > BOOKING_TTL]:
            del _bookings[k]
        if value is not None:
            _bookings[key] = (now, value)
            return value
        hit = _bookings.get(key)
        return hit[1] if hit else None


def _trial_allowed(ip):
    now = time.time()
    with _trial_lock:
        q = _trial_recent[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= TRIAL_PER_HOUR:
            return False
        q.append(now)
        return True


class LeadHandler(BaseHTTPRequestHandler):
    #: Send the emails before answering (serverless: the process may be frozen
    #: the moment the answer is out) or after it, on a thread (a long-lived server).
    send_in_background = True
    #: The one path this handler answers; None = both (the local server).
    only = None

    def _answer(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in lead_mail.cors_headers(self.headers.get("Origin")).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _route(self):
        p = self.path.split("?")[0].rstrip("/")
        if self.only:                      # a Vercel function: its file decides the path
            return self.only
        return {"/api/leads": "leads", "/api/trial": "trial"}.get(p)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in lead_mail.cors_headers(self.headers.get("Origin")).items():
            self.send_header(k, v)
        self.end_headers()

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > lead_mail.MAX_BODY:
            return None
        return self.rfile.read(length) if length else b""

    def _hosts(self):
        """The host the visitor's request came to (a local preview passes it on
        as X-Forwarded-Host); used for the email's link in development only."""
        return [self.headers.get("X-Forwarded-Host") or "", self.headers.get("Host") or ""]

    def _ip(self):
        return (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or self.client_address[0]

    def do_POST(self):
        route = self._route()
        if route is None:
            return self._answer(404, {"detail": "Not found"})
        raw = self._body()
        if raw is None:
            return self._answer(413, {"detail": "Too large"})
        if route == "trial":
            return self._trial(raw)
        try:
            lead = lead_mail.accept(raw, self._ip())
        except lead_mail.LeadError as e:
            body = {"detail": e.detail}
            if e.fields:
                body["fields"] = e.fields
            return self._answer(e.status, body)
        out = {"ok": True}
        # a demo slot booked with the form: the free trial — its sign-up pass,
        # then (only once the pass exists) the email with the access link
        if lead.get("slot") and lead["type"] in ("contact", "demo") and trial.configured():
            return self._answer(200, self._book_trial(lead))
        if lead.get("slot") and lead["type"] in ("contact", "demo"):
            out["trial_error"] = "trial_not_configured"
        if self.send_in_background:
            threading.Thread(target=lead_mail.send_all, args=(lead,), daemon=True).start()
        else:
            lead_mail.send_all(lead)
        return self._answer(200, out)

    def _book_trial(self, lead):
        key = (lead["email"].lower(), lead["slot"])
        done = _booking(key)
        if done:
            out = dict(done["out"])
            if not out.get("email_sent") and done.get("access"):     # the first email failed: try again
                out["email_sent"] = lead_mail.send_trial(done["lead"], done["access"])
                _booking(key, {**done, "out": out})
            return out
        try:
            t = trial.ticket(lead["email"], lead["slot"], name=lead["name"])
        except trial.TrialError as e:
            return {"ok": True, "trial_error": e.detail}
        lead["trial_window"], lead["trial_minutes"] = t["window"], t["minutes"]
        access = trial.access_url(t["ticket"], self._hosts())
        if not access:
            print("[lead api] no demo address for the trial email's link: set DEMO_PUBLIC_URL", flush=True)
        sent = lead_mail.send_trial(lead, access) if access else False
        out = {"ok": True, "trial": t, "email_sent": sent}
        _booking(key, {"out": out, "lead": lead, "access": access})
        if self.send_in_background:
            threading.Thread(target=lead_mail.send_notify, args=(lead,), daemon=True).start()
        else:
            lead_mail.send_notify(lead)
        return out

    def _resend(self, ticket_token):
        """The free-trial email again, for whoever holds the trial's pass."""
        try:
            d = trial.resend_details(ticket_token)
        except trial.TrialError as e:
            return self._answer(e.status, {"detail": e.detail})
        if not lead_mail._allowed("email:" + d["email"]):
            return self._answer(429, {"detail": "too_many_attempts"})
        access = trial.access_url(ticket_token, self._hosts())
        lead = {"name": d["name"], "email": d["email"], "trial_window": d["window"], "trial_minutes": d["minutes"]}
        return self._answer(200, {"ok": True, "email_sent": lead_mail.send_trial(lead, access) if access else False})

    def _trial(self, raw):
        if not _trial_allowed(self._ip()):
            return self._answer(429, {"detail": "too_many_attempts"})
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            data = None
        if isinstance(data, dict) and data.get("action") == "resend":
            return self._resend(data.get("ticket"))
        try:
            return self._answer(200, trial.handle(raw))
        except trial.TrialError as e:
            return self._answer(e.status, {"detail": e.detail, **e.extra})

    def _refuse(self):
        self._answer(405, {"detail": "Only POST"})

    do_GET = do_PUT = do_PATCH = do_DELETE = _refuse

    def log_message(self, fmt, *args):
        print("[lead api] " + (fmt % args), flush=True)
