"""POST /api/trial on Vercel — the demo's sign-up, sign-in and session check
(lead-api/trial.py). Needs DEMO_TRIAL_SECRET in the project's environment."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lead-api"))
from http_handler import LeadHandler  # noqa: E402


class handler(LeadHandler):  # Vercel's Python runtime serves the class called `handler`
    only = "trial"
