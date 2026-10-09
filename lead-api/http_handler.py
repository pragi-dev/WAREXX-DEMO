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
            return self._answer(e.status, {"detail": e.detail})
        out = {"ok": True}
        # a demo slot booked with the form: the pass to sign up for it
        if lead.get("slot") and lead["type"] in ("contact", "demo"):
            try:
                out["trial"] = trial.ticket(lead["email"], lead["slot"])
                lead["trial_window"] = out["trial"]["window"]
            except trial.TrialError as e:
                out["trial_error"] = e.detail
        if self.send_in_background:
            threading.Thread(target=lead_mail.send_all, args=(lead,), daemon=True).start()
        else:
            lead_mail.send_all(lead)
        return self._answer(200, out)

    def _trial(self, raw):
        if not _trial_allowed(self._ip()):
            return self._answer(429, {"detail": "too_many_attempts"})
        try:
            return self._answer(200, trial.handle(raw))
        except trial.TrialError as e:
            return self._answer(e.status, {"detail": e.detail, **e.extra})

    def _refuse(self):
        self._answer(405, {"detail": "Only POST"})

    do_GET = do_PUT = do_PATCH = do_DELETE = _refuse

    def log_message(self, fmt, *args):
        print("[lead api] " + (fmt % args), flush=True)
