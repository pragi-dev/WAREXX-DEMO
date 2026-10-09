"""The demo trial: book a slot, sign up, two hours of access, then nothing.

    python lead-api/trial_test.py
"""
import datetime as dt
import json
import os
import sys

os.environ["DEMO_TRIAL_SECRET"] = "test-secret-0123456789abcdef"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trial  # noqa: E402

bad = []


def eq(what, got, want):
    if got != want:
        bad.append(what)
        print("  FAIL  %s\n        got  %r\n        want %r" % (what, got, want))
    else:
        print("  ok    %s" % what)


def err(fn):
    try:
        fn()
        return None
    except trial.TrialError as e:
        return (e.status, e.detail)


IST = dt.timezone(dt.timedelta(minutes=330))
NOW = dt.datetime(2026, 10, 9, 9, 30, tzinfo=IST).timestamp()      # Fri 9 Oct, 09:30 IST
H = 3600

print("slots")
eq("'now' opens a two-hour window from now", trial.slot_window("now", NOW), (int(NOW), int(NOW) + 2 * H))
ten = dt.datetime(2026, 10, 9, 10, 0, tzinfo=IST)
eq("a listed slot (10:00 IST) is two hours from its start",
   trial.slot_window(ten.isoformat(), NOW), (int(ten.timestamp()), int(ten.timestamp()) + 2 * H))
eq("an hour that is not a slot is refused",
   err(lambda: trial.slot_window(dt.datetime(2026, 10, 9, 11, 0, tzinfo=IST).isoformat(), NOW)), (422, "invalid_slot"))
eq("a slot already over is refused",
   err(lambda: trial.slot_window(dt.datetime(2026, 10, 8, 10, 0, tzinfo=IST).isoformat(), NOW)), (422, "invalid_slot"))
eq("a slot more than a week ahead is refused",
   err(lambda: trial.slot_window(dt.datetime(2026, 10, 20, 10, 0, tzinfo=IST).isoformat(), NOW)), (422, "invalid_slot"))
eq("a time without a zone is refused", err(lambda: trial.slot_window("2026-10-09T10:00:00", NOW)), (422, "invalid_slot"))
eq("the window reads in IST", trial.describe(int(ten.timestamp()), int(ten.timestamp()) + 2 * H),
   "Fri 9 Oct, 10:00 AM – 12:00 PM IST")

print("sign up and sign in")
t = trial.ticket("Visitor@Example.com", "now", NOW)
eq("the ticket carries the email (lower case) and the window", (t["email"], t["end"] - t["start"]), ("visitor@example.com", 2 * H))
eq("a short password is refused", err(lambda: trial.signup(t["ticket"], "short", NOW)), (422, "weak_password"))
acc = trial.signup(t["ticket"], "correct horse", NOW + 60)
eq("signing up inside the window also signs in", trial.verify(acc["session"], NOW + 61)["ok"], True)
eq("the right password signs in", "session" in trial.login(acc["account"], "visitor@example.com", "correct horse", NOW + 600), True)
eq("the email is matched without case", "session" in trial.login(acc["account"], "VISITOR@example.com", "correct horse", NOW + 600), True)
eq("a wrong password is refused",
   err(lambda: trial.login(acc["account"], "visitor@example.com", "wrong horse", NOW + 600)), (401, "wrong_password"))
eq("another email with the right password is refused",
   err(lambda: trial.login(acc["account"], "other@example.com", "correct horse", NOW + 600)), (401, "wrong_password"))

print("two hours, then the password stops working")
eq("one minute before the end, signing in works",
   "session" in trial.login(acc["account"], "visitor@example.com", "correct horse", NOW + 2 * H - 60), True)
eq("at the end, the password is refused as expired",
   err(lambda: trial.login(acc["account"], "visitor@example.com", "correct horse", NOW + 2 * H)), (403, "expired"))
s = trial.login(acc["account"], "visitor@example.com", "correct horse", NOW + 600)
eq("a session checks out inside the window", trial.verify(s["session"], NOW + 601)["ok"], True)
eq("…and is refused after it, even mid-visit", err(lambda: trial.verify(s["session"], NOW + 2 * H + 1)), (403, "expired"))
eq("a ticket cannot be used to sign up after its window",
   err(lambda: trial.signup(t["ticket"], "correct horse", NOW + 2 * H + 5)), (403, "expired"))

print("a later slot")
t2 = trial.ticket("v@example.com", ten.isoformat(), NOW)
a2 = trial.signup(t2["ticket"], "correct horse", NOW)
eq("signing up before the slot gives an account and a session", ("account" in a2, "session" in a2), (True, True))
eq("…which does not open the demo before the slot starts",
   err(lambda: trial.verify(a2["session"], NOW)), (403, "not_yet"))
eq("…and does once it starts", trial.verify(a2["session"], ten.timestamp() + 5)["ok"], True)
eq("signing in before the slot also waits for it",
   err(lambda: trial.verify(trial.login(a2["account"], "v@example.com", "correct horse", NOW)["session"], NOW)), (403, "not_yet"))

print("passes cannot be forged")
body, mac = acc["account"].split(".")
p = json.loads(trial._unb64(body))
p["x"] += 10 * H
forged = trial._b64(json.dumps(p, separators=(",", ":"), sort_keys=True).encode()) + "." + mac
eq("an account with its end moved is refused",
   err(lambda: trial.login(forged, "visitor@example.com", "correct horse", NOW + 3 * H)), (401, "invalid_pass"))
eq("a ticket is not a session", err(lambda: trial.verify(t["ticket"], NOW)), (401, "invalid_pass"))
eq("rubbish is refused", err(lambda: trial.verify("abc", NOW)), (401, "invalid_pass"))
os.environ["DEMO_TRIAL_SECRET"] = "a-different-secret-entirely-xyz"
eq("a pass signed with another secret is refused", err(lambda: trial.verify(s["session"], NOW + 601)), (401, "invalid_pass"))
os.environ["DEMO_TRIAL_SECRET"] = ""
eq("…and the status check says so, so the demo opens without a sign-up",
   trial.handle(b'{"action": "status"}'), {"configured": False})
eq("without a secret the trial says it is not set up", err(lambda: trial.ticket("v@example.com", "now", NOW)),
   (503, "trial_not_configured"))

if bad:
    print("\n%d FAILED" % len(bad))
    sys.exit(1)
print("\nall passing")
