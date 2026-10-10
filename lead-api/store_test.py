"""The leads database and the admin API, end to end through the real handler.

    python lead-api/store_test.py                          (SQLite, nothing needed)
    LEADS_DATABASE_URL=postgresql://… python lead-api/store_test.py   (PostgreSQL; needs pg8000)

Covers: every form kept; a free trial and its lead; duplicates not kept twice;
sign-up, sign-in and the tour recorded against the trial (and read back); the
admin's password, token, lists, search, filters and CSV; a database that is down
never breaking a form.
"""
import csv
import io
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

TMP = tempfile.mkdtemp(prefix="warexx-store-")
os.environ.update({"DEMO_TRIAL_SECRET": "test-secret-for-the-store-0123456789abcdef",
                   "LEAD_MAIL_BACKEND": "console", "LEAD_MAIL_DIR": os.path.join(TMP, "mail"),
                   "LEADS_SQLITE_PATH": os.path.join(TMP, "leads.db")})
for k in ("ADMIN_PASSWORD", "POSTGRES_URL", "DATABASE_URL", "DEMO_PUBLIC_URL"):
    os.environ.pop(k, None)
PG = os.environ.get("LEADS_DATABASE_URL", "")
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


def post(path, body, ip="10.1.0.1", headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Forwarded-For": ip, **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def rows(sql, params=()):
    return store.run(sql, params, fetch=True)


print(f"database: {store.kind()}" + (" (" + PG.split("@")[-1] + ")" if PG else ""))
if PG:                                     # a fresh start on the test database
    store.run("DROP TABLE IF EXISTS leads"); store.run("DROP TABLE IF EXISTS trials")
    store._ready.clear()

print("every form is kept")
code, _ = post("/api/leads", {"type": "brochure", "name": "Meena", "email": "Meena@Example.com", "phone": "9999988888", "company": "M Co"})
r = rows("SELECT type, name, email, trial_ref FROM leads")
ok(code == 200 and len(r) == 1 and r[0]["type"] == "brochure" and r[0]["email"] == "meena@example.com",
   "a brochure request is in leads (email lower-cased)")

print("a free trial")
form = {"type": "demo", "name": "Asha Rao", "email": "asha@example.com", "company": "Rao Textiles",
        "phone": "+91 98765 43210", "improve": "Inventory", "interest": "Inventory", "slot": "now"}
code, b = post("/api/leads", form)
tk = b.get("trial", {}).get("ticket", "")
t = rows("SELECT * FROM trials")
ok(code == 200 and len(t) == 1 and t[0]["email"] == "asha@example.com" and t[0]["name"] == "Asha Rao", "the trial is in trials")
ok(t and t[0]["email_sent"] == 1 and t[0]["window_text"], "with its window and the email marked sent")
lead = rows("SELECT * FROM leads WHERE type = 'demo'")
ok(len(lead) == 1 and lead[0]["trial_ref"] == t[0]["ref"] and lead[0]["company"] == "Rao Textiles", "and the form that asked for it, linked to it")
code, _ = post("/api/leads", form)
ok(len(rows("SELECT ref FROM trials")) == 1 and len(rows("SELECT id FROM leads WHERE type = 'demo'")) == 1,
   "the same request again (a double click) is not kept twice")

print("what happened to the trial")
code, s = post("/api/trial", {"action": "signup", "ticket": tk, "password": "a-good-password"})
ok(code == 200 and "ref" not in s, "sign-up works (and the internal reference is not handed out)")
ok(rows("SELECT signed_up_at FROM trials")[0]["signed_up_at"], "…and is recorded")
post("/api/trial", {"action": "login", "account": s["account"], "email": "asha@example.com", "password": "a-good-password"})
post("/api/trial", {"action": "login", "account": s["account"], "email": "asha@example.com", "password": "a-good-password"})
x = rows("SELECT logins, last_login_at FROM trials")[0]
ok(x["logins"] == 2 and x["last_login_at"], "sign-ins are counted")
code, v = post("/api/trial", {"action": "verify", "session": s["session"]})
ok(code == 200 and v.get("tour_state") is None, "the session check carries the tour state (none yet)")
code, _ = post("/api/trial", {"action": "tour", "session": s["session"], "state": "skipped"})
code, v = post("/api/trial", {"action": "verify", "session": s["session"]})
ok(v.get("tour_state") == "skipped", "a skipped tour is kept against the trial, and read back on the next check")
code, _ = post("/api/trial", {"action": "tour", "session": s["session"], "state": "deleted"})
ok(code == 422, "only completed / skipped are accepted")
code, _ = post("/api/trial", {"action": "tour", "session": "forged.pass", "state": "completed"})
ok(code == 401, "a forged session cannot set anyone's tour")
post("/api/trial", {"action": "resend", "ticket": tk})
ok(rows("SELECT emails_sent FROM trials")[0]["emails_sent"] == 2, "a resent email is counted")

print("the admin")
code, j = post("/api/admin", {"action": "login", "password": "x"})
ok(code == 503 and j.get("detail") == "admin_not_configured", "switched off without ADMIN_PASSWORD")
os.environ["ADMIN_PASSWORD"] = "correct horse battery"
code, j = post("/api/admin", {"action": "leads"})
ok(code == 401, "no token, no data")
code, j = post("/api/admin", {"action": "login", "password": "wrong password"}, ip="10.9.9.9")
ok(code == 401 and j.get("detail") == "wrong_password", "a wrong password is refused")
code, j = post("/api/admin", {"action": "login", "password": "correct horse battery"})
tok = j.get("token", "")
ok(code == 200 and tok, "the right password gives a token")
code, j = post("/api/admin", {"action": "summary", "token": tok})
ok(j.get("leads") == 2 and j.get("trials") == 1 and j.get("signed_up") == 1 and j.get("database") == store.kind(), f"summary: {j}")
code, j = post("/api/admin", {"action": "leads", "token": tok})
ok(j.get("total") == 2 and j["rows"][0]["type"] == "demo", "leads, newest first")
code, j = post("/api/admin", {"action": "leads", "token": tok, "q": "rao"})
ok(j.get("total") == 1 and j["rows"][0]["email"] == "asha@example.com", "search by company")
code, j = post("/api/admin", {"action": "leads", "token": tok, "type": "brochure"})
ok(j.get("total") == 1 and j["rows"][0]["name"] == "Meena", "filter by form")
code, j = post("/api/admin", {"action": "trials", "token": "", "status": "signed_up"}, headers={"Authorization": "Bearer " + tok})
ok(code == 200 and j.get("total") == 1 and j["rows"][0]["tour_state"] == "skipped", "trial users (token in the Authorization header too)")
code, j = post("/api/admin", {"action": "trials", "token": tok, "status": "not_signed_up"})
ok(j.get("total") == 0, "filter: not signed up yet")
post("/api/leads", {"type": "contact", "name": "=HYPERLINK(\"http://x\")", "email": "evil@example.com", "message": "+cmd"}, ip="10.1.0.5")
code, j = post("/api/admin", {"action": "export", "token": tok, "what": "leads"})
table = list(csv.reader(io.StringIO(j.get("csv", ""))))
evil = next((r for r in table if "evil@example.com" in r), [])
ok(code == 200 and table[0][:3] == ["id", "created_at", "type"] and len(table) == 4, "CSV export of every lead")
ok(evil and evil[3].startswith("'=") and any(c.startswith("'+") for c in evil), "a formula in a visitor's text is defused in the CSV")
code, j = post("/api/admin", {"action": "leads", "token": tok[:-2] + "xx"})
ok(code == 401, "an altered token is refused")
os.environ["ADMIN_PASSWORD"] = "a new password entirely"
code, j = post("/api/admin", {"action": "leads", "token": tok})
ok(code == 401, "changing the password signs everyone out")
codes = [post("/api/admin", {"action": "login", "password": "guess"}, ip="10.7.7.7")[0] for _ in range(11)]
ok(codes[-1] == 429, "repeated wrong passwords are slowed down")

print("a database that is down")
os.environ["LEADS_DATABASE_URL"] = "postgresql://nobody@127.0.0.1:1/none"
code, b = post("/api/leads", {**form, "email": "down@example.com", "name": "Down"}, ip="10.1.0.9")
ok(code == 200 and b.get("trial") and b.get("email_sent") is True, "the form, the trial and the email still work")
os.environ["ADMIN_PASSWORD"] = "x-password-x"
code, j = post("/api/admin", {"action": "login", "password": "x-password-x"}, ip="10.6.6.6")
code, j = post("/api/admin", {"action": "leads", "token": j.get("token", "")})
ok(code == 503 and j.get("detail") == "database_unavailable", "the admin says the database is unavailable, without internals")

srv.shutdown()
print(f"\n{bad} FAILED" if bad else "\nall passing")
sys.exit(1 if bad else 0)
