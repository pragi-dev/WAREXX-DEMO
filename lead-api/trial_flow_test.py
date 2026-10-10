"""The free-trial request end to end, against the real HTTP handler:
registration → the sign-up pass → the email with the access link → sign-up.

    python lead-api/trial_flow_test.py

Runs the handler on a local port with a test secret, saving emails to a
temporary folder (console mode) instead of sending them.
"""
import glob
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

OUTBOX = tempfile.mkdtemp(prefix="warexx-trial-mail-")
os.environ.update({"DEMO_TRIAL_SECRET": "test-secret-for-the-trial-flow-0123456789",
                   "LEAD_MAIL_BACKEND": "console", "LEAD_MAIL_DIR": OUTBOX,
                   "LEAD_SUPPORT_EMAIL": "support@example.com", "LEAD_COMPANY_NAME": "WAREXX",
                   "LEADS_SQLITE_PATH": os.path.join(OUTBOX, "leads.db")})   # never the real leads file
for _k in ("LEADS_DATABASE_URL", "POSTGRES_URL", "DATABASE_URL"):
    os.environ.pop(_k, None)
os.environ.pop("DEMO_PUBLIC_URL", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import http_handler  # noqa: E402
import lead_mail  # noqa: E402
import trial  # noqa: E402

bad = 0


def ok(cond, what):
    global bad
    print(f"  {'ok  ' if cond else 'FAIL'}  {what}")
    if not cond:
        bad += 1


class H(http_handler.LeadHandler):
    send_in_background = False

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def post(path, body, ip="10.0.0.1"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Forwarded-For": ip})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def mails():
    out = []
    for f in sorted(glob.glob(os.path.join(OUTBOX, "*.eml"))):
        with open(f, "rb") as fh:
            out.append(fh.read().decode("utf-8", "replace"))
    return out


def trial_mails():
    return [m for m in mails() if "Your Free Trial Is Ready" in m]


form = {"type": "demo", "name": "Asha Rao", "email": "asha@example.com", "company": "Rao Textiles",
        "phone": "+91 98765 43210", "improve": "Inventory", "slot": "now"}

print("validation")
code, body = post("/api/leads", {**form, "email": "not-an-email", "phone": "12", "company": "", "slot": ""}, ip="10.0.0.9")
ok(code == 422, f"a bad form is refused (HTTP {code})")
ok(set(body.get("fields", {})) == {"email", "phone", "company", "slot"},
   f"…naming each wrong field: {sorted(body.get('fields', {}))}")
ok("Traceback" not in json.dumps(body) and "trial" not in body, "…with no internals and no pass")
ok(not trial_mails(), "…and no email is sent")

print("registration")
code, body = post("/api/leads", form)
ok(code == 200 and body.get("trial", {}).get("ticket"), f"a good form gets a trial pass (HTTP {code})")
ok(body.get("email_sent") is True, "the access email was sent")
tk = body["trial"]["ticket"]
ok(body["trial"]["minutes"] == trial.WINDOW // 60, f"the trial length is reported ({body['trial']['minutes']} min)")
ok(len(trial_mails()) == 1, "exactly one trial email")

print("the email")
m = trial_mails()[0] if trial_mails() else ""
import email  # noqa: E402
msg = email.message_from_string(m)
ok(msg["To"] == "asha@example.com", f"to the registered address ({msg['To']})")
ok("Your Free Trial Is Ready" in (msg["Subject"] or ""), f"subject: {msg['Subject']}")
html_part = next((p.get_payload(decode=True).decode() for p in msg.walk() if p.get_content_type() == "text/html"), "")
text_part = next((p.get_payload(decode=True).decode() for p in msg.walk() if p.get_content_type() == "text/plain"), "")
link = f"http://127.0.0.1:{PORT}/demo/#t={tk}"
ok("Access Your Free Trial" in html_part and link in html_part, "an “Access Your Free Trial” button with the demo link + the pass")
ok(link in text_part, "…and the same link in the plain-text part")
ok("asha@example.com" in text_part and f"http://127.0.0.1:{PORT}/demo/" in text_part, "the demo URL and the login email")
ok("support@example.com" in text_part and "WAREXX Team" in text_part, "support contact and sign-off")
ok("tour" in text_part and "skip" in text_part, "mentions the guided tour and that it can be skipped")
ok("We never send passwords by email" in text_part and "password:" not in text_part.lower().replace("how to sign in", ""),
   "no password in the email — the visitor chooses one")
ok(("2 hours" in text_part) or ("minutes" in text_part), "the trial length")

print("retries and double clicks")
before = len(mails())
code, again = post("/api/leads", form)
ok(code == 200 and again.get("trial", {}).get("ticket") == tk, "the same request again returns the same trial")
ok(len(mails()) == before, "…and sends nothing a second time")

print("email failure, then a safe retry")
orig = lead_mail._deliver
lead_mail._deliver = lambda m: (_ for _ in ()).throw(OSError("smtp down"))
form2 = {**form, "email": "ravi@example.com", "name": "Ravi K"}
code, b2 = post("/api/leads", form2, ip="10.0.0.2")
ok(code == 200 and b2.get("trial") and b2.get("email_sent") is False, "a failed email is reported (email_sent: false), the trial still exists")
lead_mail._deliver = orig
code, b3 = post("/api/leads", form2, ip="10.0.0.2")
ok(b3.get("trial", {}).get("ticket") == b2["trial"]["ticket"] and b3.get("email_sent") is True,
   "retrying sends the email now — same trial, no duplicate")

print("resend")
before = len(trial_mails())
code, r = post("/api/trial", {"action": "resend", "ticket": tk})
ok(code == 200 and r.get("email_sent") is True and len(trial_mails()) == before + 1, "the pass holder can have the email sent again")
code, r = post("/api/trial", {"action": "resend", "ticket": tk[:-3] + "abc"})
ok(code == 401, f"a forged pass cannot (HTTP {code})")

print("a forged Host cannot put its own address in the email")
before = len(trial_mails())
req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/leads",
    data=json.dumps({**form, "email": "victim@example.com", "name": "V"}).encode(),
    headers={"Content-Type": "application/json", "Host": "evil.example", "X-Forwarded-For": "10.0.0.7"})
with urllib.request.urlopen(req) as r:
    spoof = json.loads(r.read())
ok(spoof.get("email_sent") is False and len(trial_mails()) == before and not any("evil.example" in m for m in mails()),
   "no link is built from an untrusted Host, and no email with one is sent")

print("the link signs up")
code, s = post("/api/trial", {"action": "signup", "ticket": tk, "password": "a-good-password"})
ok(code == 200 and s.get("account") and s.get("session"), "the pass from the email creates the account")
code, v = post("/api/trial", {"action": "verify", "session": s.get("session", "")})
ok(code == 200 and v.get("email") == "asha@example.com", "…for that email only")

srv.shutdown()
print(f"\n{bad} FAILED" if bad else "\nall passing")
sys.exit(1 if bad else 0)
