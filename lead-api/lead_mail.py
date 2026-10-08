"""Leads from the public landing page — the landing site's own lead API.

Every form on the landing page (contact, book a demo, brochure, sandbox,
pre-book, plan, partner) POSTs its JSON here. The visitor gets a thank-you email
from LEAD_MAIL_FROM, and LEAD_NOTIFY_TO gets the lead's details.

This is the same behaviour as the production app's /api/leads
(app/backend/app/routers/leads.py), ported to the standard library so the
landing site can be deployed without the WAREXX backend. It has no database,
no WAREXX credentials and no route to the production API: it validates a form
and sends two emails. That is all.

Settings — SERVER-SIDE environment variables only (never VITE_*, never in the page):
    LEAD_MAIL_BACKEND   smtp to send for real; console (default) saves .eml files
    LEAD_MAIL_FROM      "WAREXX <sales@example.com>"
    LEAD_NOTIFY_TO      who receives the lead (default: the From address)
    LEAD_SMTP_HOST      e.g. smtpout.secureserver.net
    LEAD_SMTP_PORT      465 (SSL) or 587 (STARTTLS)
    LEAD_SMTP_USER      the mailbox user
    LEAD_SMTP_PASSWORD  the mailbox password
    LEAD_MAIL_DIR       where console mode saves .eml files (default lead-api/outbox)
    LEAD_ALLOWED_ORIGINS  comma-separated origins allowed to POST cross-origin
                          (only needed when the API is on another host than the page)
"""
import datetime as dt
import html
import json
import os
import re
import smtplib
import ssl
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr


def _env(name, default=""):
    return (os.environ.get(name) or "").strip() or default


MAIL_BACKEND = _env("LEAD_MAIL_BACKEND", "console")
MAIL_FROM = _env("LEAD_MAIL_FROM", "WAREXX <noreply@example.com>")
NOTIFY_TO = _env("LEAD_NOTIFY_TO", parseaddr(MAIL_FROM)[1])
SMTP_HOST = _env("LEAD_SMTP_HOST", "smtpout.secureserver.net")
SMTP_PORT = int(_env("LEAD_SMTP_PORT", "465"))
SMTP_USER = _env("LEAD_SMTP_USER", parseaddr(MAIL_FROM)[1])
SMTP_PASSWORD = _env("LEAD_SMTP_PASSWORD", "")
MAIL_DIR = _env("LEAD_MAIL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "outbox"))
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in _env("LEAD_ALLOWED_ORIGINS").split(",") if o.strip()}

#: Sends per address per hour. The endpoint is public and emails whatever
#: address it is given, so this is what stops it being used to spam someone.
#: (Per process: on a serverless host each instance counts on its own.)
PER_HOUR = 5
MAX_BODY = 16 * 1024

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_recent = defaultdict(deque)
_lock = threading.Lock()

FIELDS = ("name", "email", "company", "phone", "industry", "interest", "message",
          "type", "page", "submittedAt")

#: What the visitor asked for, as the subject and opening line of their email.
_ASKED = {
    "contact":  ("Thanks for contacting WAREXX", "Thanks for getting in touch. We’ve received your message"),
    "demo":     ("Your WAREXX demo request", "Thanks for booking a demo. We’ve received your request"),
    "brochure": ("Your WAREXX brochure", "Thanks for downloading the WAREXX brochure"),
    "sandbox":  ("Welcome to the WAREXX live demo", "Thanks for trying the WAREXX live demo"),
    "prebook":  ("Your WAREXX pre-booking", "Thanks for pre-booking WAREXX. We’ve received your request"),
    "plan":     ("Your WAREXX plan enquiry", "Thanks for your interest in a WAREXX plan"),
    "partner":  ("Your WAREXX launch partner application", "Thanks for applying to be a WAREXX launch partner"),
}


class LeadError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _allowed(key):
    now = time.time()
    with _lock:
        q = _recent[key]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= PER_HOUR:
            return False
        q.append(now)
        return True


def parse(raw: bytes) -> dict:
    """The form's JSON as a lead, or LeadError. Only known fields, only strings."""
    if len(raw) > MAX_BODY:
        raise LeadError(413, "Too large")
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise LeadError(400, "Send the form as JSON")
    if not isinstance(data, dict):
        raise LeadError(400, "Send the form as JSON")
    lead = {k: (str(data[k])[:2000] if data.get(k) is not None else None) for k in FIELDS}
    lead["name"] = (lead["name"] or "").strip()
    lead["email"] = (lead["email"] or "").strip()
    lead["type"] = lead["type"] or "contact"
    if not lead["name"] or not _EMAIL.match(lead["email"]):
        raise LeadError(422, "A name and a valid email are needed")
    return lead


def accept(raw: bytes, ip: str) -> dict:
    """Validate, rate-limit, and send in the background. The answer goes back
    before any email is sent: a slow mail server must not keep a visitor waiting."""
    lead = parse(raw)
    if not (_allowed("ip:" + (ip or "?")) and _allowed("email:" + lead["email"].lower())):
        raise LeadError(429, "Too many requests — please try again later")
    return lead


def _wrap(body_html):
    return ('<div style="font:15px/1.6 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
            'color:#1A1814;max-width:560px;margin:0 auto;padding:24px">'
            f'{body_html}'
            '<p style="color:#6E675E;font-size:13px;margin-top:28px;border-top:1px solid #E6E1D8;'
            'padding-top:14px">Team WAREXX — Built for Smarter Operations.</p></div>')


def _message(to, subject, text_body, html_body):
    m = EmailMessage()
    m["From"] = MAIL_FROM
    m["To"] = to
    m["Reply-To"] = parseaddr(MAIL_FROM)[1]
    m["Subject"] = subject
    m["Message-ID"] = make_msgid(domain=parseaddr(MAIL_FROM)[1].split("@")[-1] or "localhost")
    m.set_content(text_body)
    m.add_alternative(html_body, subtype="html")
    return m


def _visitor_email(lead):
    subject, opening = _ASKED.get(lead["type"], _ASKED["contact"])
    first = (lead["name"].split() or ["there"])[0]
    text_body = (f"Hi {first},\n\n{opening}, and someone from our team will get back to you "
                 "shortly.\n\nIf there’s anything you’d like to add, just reply to this email.\n\n"
                 "Team WAREXX\n")
    body = (f"<p>Hi {html.escape(first)},</p>"
            f"<p>{html.escape(opening)}, and someone from our team will get back to you shortly.</p>"
            "<p>If there’s anything you’d like to add, just reply to this email.</p>")
    return _message(lead["email"], subject, text_body, _wrap(body))


def _notify_email(lead):
    rows = [("Form", lead["type"]), ("Name", lead["name"]), ("Email", lead["email"]),
            ("Phone", lead["phone"]), ("Company", lead["company"]), ("Industry", lead["industry"]),
            ("Interested in", lead["interest"]), ("Message", lead["message"]),
            ("Submitted", lead["submittedAt"])]
    table = "".join(f'<tr><td style="padding:3px 16px 3px 0;color:#6E675E">{k}</td>'
                    f"<td>{html.escape(str(v or '—'))}</td></tr>" for k, v in rows)
    m = _message(NOTIFY_TO, f"New WAREXX lead ({lead['type']}): {lead['company'] or lead['name']}",
                 "\n".join(f"{k}: {v or '-'}" for k, v in rows) + "\n",
                 _wrap(f"<p>A visitor sent the landing page form.</p><table>{table}</table>"))
    m.replace_header("Reply-To", lead["email"])
    return m


def _deliver(m):
    if MAIL_BACKEND == "smtp":
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30,
                                  context=ssl.create_default_context()) as s:
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.send_message(m)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.send_message(m)
        print(f"[lead mail] sent to={m['To']} subject={m['Subject']!r}", flush=True)
        return
    folder = MAIL_DIR
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:                       # read-only host: the platform's temp dir
        folder = os.path.join(tempfile.gettempdir(), "warexx-lead-mail")
        os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.eml")
    with open(path, "wb") as f:
        f.write(bytes(m))
    print(f"[lead mail] (console, not sent) to={m['To']} subject={m['Subject']!r} saved={path}", flush=True)


def send_all(lead):
    for build in (_visitor_email, _notify_email):
        try:
            _deliver(build(lead))
        except Exception as exc:                      # noqa: BLE001 — logged, nothing to retry into
            print(f"[lead mail] FAILED {build.__name__} for {lead['email']}: {type(exc).__name__}: {exc}",
                  flush=True)


def cors_headers(origin):
    """CORS only for origins named in LEAD_ALLOWED_ORIGINS; same-origin needs none."""
    if origin and origin.rstrip("/") in ALLOWED_ORIGINS:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "600"}
    return {}
