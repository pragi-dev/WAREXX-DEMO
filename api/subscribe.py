"""The landing site's payment endpoint on Vercel (lead-api/payments.py through
lead-api/http_handler.py, route "subscribe"). Settings: Vercel environment variables
(server-side) — see docs/PAYMENTS.md. Never a VITE_* variable."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lead-api"))
from http_handler import LeadHandler  # noqa: E402


class handler(LeadHandler):  # Vercel's Python runtime serves the class called `handler`
    send_in_background = False
    only = "subscribe"
