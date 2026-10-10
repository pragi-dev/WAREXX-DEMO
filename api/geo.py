"""GET /api/geo on Vercel — the visitor's country, so the landing page can show
prices in their currency (frontend/landing/extras/local-pricing.js).

Vercel puts the country it located the visitor's IP address in into the
x-vercel-ip-country header; this hands back just that two-letter code. Nothing
is stored or logged, and nothing else about the visitor is returned. Anywhere
without the header (a local server), the answer is an empty country and the
page keeps the currency it chose from the browser's own time zone.
"""
import json
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):  # Vercel's Python runtime serves the class called `handler`
    def do_GET(self):
        country = (self.headers.get("x-vercel-ip-country") or "").strip().upper()
        if len(country) != 2 or not country.isalpha():
            country = ""
        body = json.dumps({"country": country}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        # per visitor: never cached by a CDN or shared between visitors
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # no request logs: nothing about visitors is kept
        pass
