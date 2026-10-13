"""Emailed login details (DEMO_EMAIL_PASSWORD=1), end to end through the real handler.

    python lead-api/credentials_test.py                         (SQLite)
    LEADS_DATABASE_URL=postgresql://… python lead-api/credentials_test.py   (PostgreSQL; needs pg8000)

Covers: the trial email carries the login email + a generated password (and the
link); the password is never in the answer, the sales email or the database
(only its hash); signing in with it on any device; wrong / unknown / expired;
a resend replaces the password; a database that is down; an older database
without the password columns is upgraded.
"""
import email
import glob
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

TMP = tempfile.mkdtemp(prefix="warexx-creds-")
OUTBOX = os.path.join(TMP, "mail")
DB = os.path.join(TMP, "leads.db")
os.environ.update({"DEMO_TRIAL_SECRET": "test-secret-for-credentials-0123456789abcdef",
                   "LEAD_MAIL_BACKEND": "console", "LEAD_MAIL_DIR": OUTBOX, "LEADS_SQLITE_PATH": DB,
                   "DEMO_EMAIL_PASSWORD": "1", "DEMO_PUBLIC_URL": "https://demo.example.test/demo/",
                   "LEAD_NOTIFY_TO": "sales@example.com"})
for k in ("ADMIN_PASSWORD", "POSTGRES_URL", "DATABASE_URL"):
    os.environ.pop(k, None)
PG = os.environ.get("LEADS_DATABASE_URL", "")

if not PG:   # a database from before the password columns: it must be upgraded in place
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE trials (ref TEXT PRIMARY KEY, created_at TEXT NOT NULL, email TEXT NOT NULL, name TEXT,
                 company TEXT, phone TEXT, window_start INTEGER, window_end INTEGER, window_text TEXT,
                 email_sent INTEGER DEFAULT 0, emails_sent INTEGER DEFAULT 0, signed_up_at TEXT,
                 last_login_at TEXT, logins INTEGER DEFAULT 0, tour_state TEXT, tour_at TEXT)""")
    c.execute("INSERT INTO trials (ref, created_at, email) VALUES ('old1', '2026-01-01', 'old@example.com')")
    c.commit(); c.close()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import http_handler  # noqa: E402
import store  # noqa: E402

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


def post(path, body, ip="10.2.0.1"):
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
            m = email.message_from_bytes(fh.read())
        text = next((p.get_payload(decode=True).decode() for p in m.walk() if p.get_content_type() == "text/plain"), "")
        html = next((p.get_payload(decode=True).decode() for p in m.walk() if p.get_content_type() == "text/html"), "")
        out.append({"to": m["To"], "subject": m["Subject"], "text": text, "html": html})
    return out


def password_in(m):
    x = re.search(r"Password: (\S+)", m["text"])
    return x.group(1) if x else None


print(f"database: {store.kind()}")
if PG:
    store.reset()

print("status")
code, s = post("/api/trial", {"action": "status"})
ok(s.get("credentials") is True, "the demo is told to ask for the emailed login details")

print("booking a trial")
form = {"type": "demo", "name": "Asha Rao", "email": "Asha@Example.com", "company": "Rao Textiles",
        "phone": "+91 98765 43210", "interest": "Inventory", "slot": "now"}
code, b = post("/api/leads", form)
trial_mails = [m for m in mails() if "Free Trial" in (m["subject"] or "")]
m = trial_mails[0] if trial_mails else {"text": "", "html": "", "to": ""}
pw = password_in(m)
ok(code == 200 and b.get("email_sent") is True and b["trial"].get("credentials") is True, "booked, emailed, credentials: true")
ok(m["to"].lower() == "asha@example.com" and "Login email: asha@example.com" in m["text"], "the email has the login email")
ok(pw and re.fullmatch(r"[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}", pw), f"…a generated password ({len(pw or '')} characters)")
ok(pw and pw in m["html"] and "https://demo.example.test/demo/#t=" in m["text"], "…in the HTML part too, with the demo link")
ok("We never send passwords" not in m["text"], "…and no longer says passwords are never emailed")
ok(pw and pw not in json.dumps(b), "the password is not in the answer to the browser")
sales = [x for x in mails() if x["to"] == "sales@example.com"]
ok(sales and pw and all(pw not in x["text"] + x["html"] for x in sales), "nor in the sales notification")
row = store.run("SELECT * FROM trials WHERE email = %s", ("asha@example.com",), fetch=True)[0]
ok(row["pw_hash"] and row["pw_salt"] and pw not in json.dumps(row), "the database keeps only a salted hash")
ok(row["email_sent"] == 1 and row["emails_sent"] == 1, "…and that the email went")

print("signing in")
code, r = post("/api/trial", {"action": "signin", "email": "asha@example.com", "password": "Wrong-Pass-1234"})
ok(code == 401 and r.get("detail") == "wrong_password", "a wrong password is refused")
code, r = post("/api/trial", {"action": "signin", "email": "nobody@example.com", "password": pw})
ok(code == 401 and r.get("detail") == "wrong_password", "an unknown email gets the same answer")
code, r = post("/api/trial", {"action": "signin", "email": "  ASHA@example.com ", "password": pw})
ok(code == 200 and r.get("session") and "ref" not in r, "the right email + password signs in (any case, any device)")
code, v = post("/api/trial", {"action": "verify", "session": r.get("session", "")})
ok(code == 200 and v.get("email") == "asha@example.com", "…and the session opens the demo")
row = store.run("SELECT signed_up_at, logins FROM trials WHERE email = %s", ("asha@example.com",), fetch=True)[0]
ok(row["signed_up_at"] and row["logins"] == 1, "the sign-in is recorded (signed up, 1 sign-in)")

print("send it again")
code, r = post("/api/trial", {"action": "resend", "ticket": b["trial"]["ticket"]})
pw2 = next(iter({password_in(x) for x in mails() if "Free Trial" in (x["subject"] or "") and x["to"].lower() == "asha@example.com"} - {pw, None}), None)
ok(code == 200 and r.get("email_sent") and pw2 and pw2 != pw, "a resend emails a new password")
ok(post("/api/trial", {"action": "signin", "email": "asha@example.com", "password": pw})[0] == 401, "…the old one stops working")
ok(post("/api/trial", {"action": "signin", "email": "asha@example.com", "password": pw2})[0] == 200, "…the new one works")

print("the database, precisely")
c = store.run("SELECT * FROM contacts WHERE email = %s", ("asha@example.com",), fetch=True)
ok(len(c) == 1 and c[0]["forms"] == 1 and c[0]["company"], "one contact for the person, with their details")
t = store.run("SELECT contact_id, password_issued_at FROM trials WHERE email = %s", ("asha@example.com",), fetch=True)[0]
ok(t["contact_id"] == c[0]["id"] and t["password_issued_at"], "the trial is linked to the contact; when its password was issued")
e = store.run("SELECT kind, status, with_password FROM email_log WHERE recipient = %s ORDER BY id", ("asha@example.com",), fetch=True)
ok([x["kind"] for x in e] == ["trial", "resend"] and all(x["status"] == "saved" and x["with_password"] for x in e),
   "each trial email is logged (saved as .eml, with a password)")
s_ = store.run("SELECT ok, reason, trial_ref FROM signin_log WHERE email = %s ORDER BY id", ("asha@example.com",), fetch=True)
ok(any(x["ok"] and x["trial_ref"] for x in s_) and any((not x["ok"]) and x["reason"] == "wrong_password" for x in s_),
   "sign-ins are logged: successes against their trial, failures with the reason")

print("after the window")
store.run("UPDATE trials SET window_start = 0, window_end = 1000 WHERE email = %s", ("asha@example.com",))
code, r = post("/api/trial", {"action": "signin", "email": "asha@example.com", "password": pw2})
ok(code == 403 and r.get("detail") == "expired", "the password no longer opens the demo")

if not PG:
    print("an older database")
    old = store.run("SELECT ref, pw_hash FROM trials WHERE ref = 'old1'", fetch=True)
    ok(old and old[0]["pw_hash"] is None, "a table made before the password columns was upgraded, its rows kept")

print("a database that is down")
os.environ["LEADS_DATABASE_URL"] = "postgresql://nobody@127.0.0.1:1/none"
before = len(mails())
code, b2 = post("/api/leads", {**form, "email": "down@example.com"}, ip="10.2.0.9")
new = [x for x in mails() if x["to"] == "down@example.com"]
ok(code == 200 and b2.get("email_sent") and b2["trial"].get("credentials") is False, "the trial is still booked and emailed")
ok(new and password_in(new[0]) is None and "#t=" in new[0]["text"], "…with the sign-up link instead of a password")
code, r = post("/api/trial", {"action": "signin", "email": "asha@example.com", "password": "x"}, ip="10.2.0.9")
ok(code == 503 and r.get("detail") == "database_unavailable", "sign-in says it is unavailable (not 'wrong password')")
if PG:
    os.environ["LEADS_DATABASE_URL"] = PG
else:
    os.environ.pop("LEADS_DATABASE_URL")

print("switched off")
os.environ["DEMO_EMAIL_PASSWORD"] = "0"
code, r = post("/api/trial", {"action": "signin", "email": "asha@example.com", "password": pw2}, ip="10.2.0.8")
ok(code == 400, "no email + password sign-in without DEMO_EMAIL_PASSWORD")
code, s = post("/api/trial", {"action": "status"}, ip="10.2.0.8")
ok(s.get("credentials") is False, "status says so")

srv.shutdown()
print(f"\n{bad} FAILED" if bad else "\nall passing")
sys.exit(1 if bad else 0)
