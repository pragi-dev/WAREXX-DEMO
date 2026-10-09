"""POST /api/leads on Vercel — the landing site's lead API as a serverless
function. The code is ../lead-api (shared with the local server.py); this file
only makes it importable and tells it to send before answering, because a
serverless process can be frozen the moment its answer is out.

Settings are Vercel environment variables (server-side): see lead-api/lead_mail.py.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lead-api"))
from http_handler import LeadHandler  # noqa: E402


class handler(LeadHandler):  # Vercel's Python runtime serves the class called `handler`
    send_in_background = False
    only = "leads"
