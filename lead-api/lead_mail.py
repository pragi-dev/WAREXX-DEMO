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
    LEAD_SUPPORT_EMAIL  the support address in the free-trial email (default: the From address)
    LEAD_COMPANY_NAME   who signs the free-trial email (default: WAREXX)
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
SUPPORT_EMAIL = _env("LEAD_SUPPORT_EMAIL", parseaddr(MAIL_FROM)[1])
COMPANY_NAME = _env("LEAD_COMPANY_NAME", "WAREXX")

#: Sends per address per hour. The endpoint is public and emails whatever
#: address it is given, so this is what stops it being used to spam someone.
#: (Per process: on a serverless host each instance counts on its own.)
PER_HOUR = 5
MAX_BODY = 16 * 1024

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_recent = defaultdict(deque)
_lock = threading.Lock()

FIELDS = ("name", "email", "company", "phone", "industry", "interest", "message",
          "type", "page", "submittedAt", "slot")

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
    def __init__(self, status, detail, fields=None):
        super().__init__(detail)
        self.status, self.detail, self.fields = status, detail, fields or {}


_PHONE = re.compile(r"^\+?[\d\s().-]{7,20}$")


def _check_trial(lead):
    """A free-trial request needs everything its form asks for — checked here
    as well as in the page, since anyone can POST to this endpoint. Answers the
    fields that are wrong, by name, so the page can mark them."""
    bad = {}
    if not lead["name"] or len(lead["name"]) > 120:
        bad["name"] = "Enter your name"
    if not _EMAIL.match(lead["email"]) or len(lead["email"]) > 254:
        bad["email"] = "Enter a valid work email"
    if not (lead.get("company") or "").strip():
        bad["company"] = "Enter your company name"
    digits = re.sub(r"\D", "", lead.get("phone") or "")
    if not _PHONE.match((lead.get("phone") or "").strip()) or not 7 <= len(digits) <= 15:
        bad["phone"] = "Enter a valid phone number"
    if not (lead.get("slot") or "").strip():
        bad["slot"] = "Choose a demo time"
    if bad:
        raise LeadError(422, "Please check the highlighted details", fields=bad)


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
    lead["trial_window"] = None        # set by the server when a demo slot is booked, never by the form
    if lead["type"] == "demo":
        _check_trial(lead)
    elif not lead["name"] or not _EMAIL.match(lead["email"]):
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
    slot = lead.get("trial_window")
    slot_text = (f"Your demo slot: {slot}. Sign up with this email address and a password of your "
                 "choice; it opens the WAREXX demo for those two hours.\n\n") if slot else ""
    text_body = (f"Hi {first},\n\n{opening}, and someone from our team will get back to you "
                 f"shortly.\n\n{slot_text}If there’s anything you’d like to add, just reply to this email.\n\n"
                 "Team WAREXX\n")
    body = (f"<p>Hi {html.escape(first)},</p>"
            f"<p>{html.escape(opening)}, and someone from our team will get back to you shortly.</p>"
            + (f"<p><b>Your demo slot: {html.escape(slot)}.</b> Sign up with this email address and a "
               "password of your choice; it opens the WAREXX demo for those two hours.</p>" if slot else "")
            + "<p>If there’s anything you’d like to add, just reply to this email.</p>")
    return _message(lead["email"], subject, text_body, _wrap(body))


def _button(url, label):
    return (f'<p style="margin:22px 0"><a href="{html.escape(url)}" style="display:inline-block;padding:13px 24px;'
            'border-radius:10px;background:#D9601B;color:#fff;font-weight:600;text-decoration:none">'
            f'{html.escape(label)}</a></p>')


def trial_email(lead, access):
    """The free-trial email: the demo is ready, how to get in, for how long.
    `access` is trial.access_url(...) — the demo with the visitor's own expiring
    sign-up pass. No password is sent: the visitor chooses one on the page the
    button opens."""
    first = (lead["name"].split() or ["there"])[0]
    window, minutes = lead.get("trial_window") or "", int(lead.get("trial_minutes") or 120)
    hours = f"{minutes // 60} hours" if minutes % 60 == 0 and minutes >= 120 else f"{minutes} minutes"
    demo_home = access.split("#")[0]
    subject = "Your Free Trial Is Ready – Access Your Demo"
    login = (f"Click “Access Your Free Trial”, then create a password of your choice on the page that opens. "
             f"From then on, sign in at {demo_home} with {lead['email']} and that password. We never send "
             "passwords by email. The link is personal to you — please don’t forward it.")
    text_body = (
        f"Hi {first},\n\nThank you for your interest in {COMPANY_NAME}!\n\n"
        "Your free trial account is ready. You can now explore the platform and experience its features "
        "through your personalised demo access.\n\n"
        f"Access your free trial: {access}\n\n"
        f"Demo URL: {demo_home}\nLogin email: {lead['email']}\n"
        f"Your trial: {hours} — {window}\n\n"
        f"How to sign in: {login}\n\n"
        "Once you’re in, a short interactive tour shows you the key features. You can skip it at any time "
        "and explore on your own.\n\n"
        f"If you need assistance, please contact {SUPPORT_EMAIL}.\n\nBest regards,\n{COMPANY_NAME} Team\n")
    rows = [("Demo URL", f'<a href="{html.escape(demo_home)}">{html.escape(demo_home)}</a>'),
            ("Login email", html.escape(lead["email"])),
            ("Your trial", html.escape(f"{hours} — {window}"))]
    table = "".join(f'<tr><td style="padding:4px 18px 4px 0;color:#6E675E;white-space:nowrap">{k}</td>'
                    f"<td>{v}</td></tr>" for k, v in rows)
    body = (f"<p>Hi {html.escape(first)},</p>"
            f"<p>Thank you for your interest in {html.escape(COMPANY_NAME)}!</p>"
            "<p><b>Your free trial account is ready.</b> You can now explore the platform and experience its "
            "features through your personalised demo access.</p>"
            + _button(access, "Access Your Free Trial")
            + f'<table style="font-size:14px;margin:0 0 14px">{table}</table>'
            f'<p style="font-size:14px"><b>How to sign in:</b> {html.escape(login)}</p>'
            "<p>Once you’re in, a short interactive tour shows you the key features. You can skip it at any "
            "time and explore on your own.</p>"
            f'<p>If you need assistance, please contact <a href="mailto:{html.escape(SUPPORT_EMAIL)}">'
            f"{html.escape(SUPPORT_EMAIL)}</a>.</p>"
            f"<p>Best regards,<br>{html.escape(COMPANY_NAME)} Team</p>")
    return _message(lead["email"], subject, text_body, _wrap(body))


def send_trial(lead, access):
    """Send the free-trial email now; True when it was handed to the mail server
    (or saved, in console mode). Never raises: a failure is logged, without the
    link, and the visitor can ask for it again."""
    try:
        _deliver(trial_email(lead, access))
        return True
    except Exception as exc:                          # noqa: BLE001
        print(f"[lead mail] FAILED trial email: {type(exc).__name__}: {exc}", flush=True)
        return False


def send_notify(lead):
    try:
        _deliver(_notify_email(lead))
    except Exception as exc:                          # noqa: BLE001
        print(f"[lead mail] FAILED notify email: {type(exc).__name__}: {exc}", flush=True)


def _notify_email(lead):
    rows = [("Form", lead["type"]), ("Name", lead["name"]), ("Email", lead["email"]),
            ("Phone", lead["phone"]), ("Company", lead["company"]), ("Industry", lead["industry"]),
            ("Interested in", lead["interest"]), ("Demo slot", lead.get("trial_window")),
            ("Message", lead["message"]),
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
