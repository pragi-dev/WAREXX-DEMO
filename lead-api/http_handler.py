"""The lead API's HTTP handling, shared by the local server (server.py) and the
Vercel function (../api/leads.py). One endpoint: POST /api/leads.

Nothing here can reach WAREXX: no database driver is imported, no WAREXX URL or
credential is read. GET, PUT, PATCH and DELETE are refused.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler

import lead_mail


class LeadHandler(BaseHTTPRequestHandler):
    #: Send the emails before answering (serverless: the process may be frozen
    #: the moment the answer is out) or after it, on a thread (a long-lived server).
    send_in_background = True

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

    def _path_ok(self):
        return self.path.split("?")[0].rstrip("/") in ("/api/leads", "")

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in lead_mail.cors_headers(self.headers.get("Origin")).items():
            self.send_header(k, v)
        self.end_headers()

    def do_POST(self):
        if not self._path_ok():
            return self._answer(404, {"detail": "Not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > lead_mail.MAX_BODY:
            return self._answer(413, {"detail": "Too large"})
        raw = self.rfile.read(length) if length else b""
        ip = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or self.client_address[0]
        try:
            lead = lead_mail.accept(raw, ip)
        except lead_mail.LeadError as e:
            return self._answer(e.status, {"detail": e.detail})
        if self.send_in_background:
            threading.Thread(target=lead_mail.send_all, args=(lead,), daemon=True).start()
        else:
            lead_mail.send_all(lead)
        return self._answer(200, {"ok": True})

    def _refuse(self):
        self._answer(405, {"detail": "Only POST /api/leads"})

    do_GET = do_PUT = do_PATCH = do_DELETE = _refuse

    def log_message(self, fmt, *args):
        print("[lead api] " + (fmt % args), flush=True)
