"""POST /api/admin on Vercel — the admin page's data (lead-api/admin.py): leads
and trial users from the database. Needs ADMIN_PASSWORD in the project's
environment; the database comes from POSTGRES_URL (Vercel Postgres / Neon)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lead-api"))
from http_handler import LeadHandler  # noqa: E402


class handler(LeadHandler):  # Vercel's Python runtime serves the class called `handler`
    only = "admin"
