"""Subscriptions through HDFC SmartGateway, end to end through the real handler,
with the gateway itself replaced by an in-process fake (hdfc._opener): no
network, and no claim about a real transaction. The real sandbox is exercised
separately (docs/PAYMENTS.md, "Sandbox test").

    python lead-api/payments_test.py                                   (SQLite)
    BILLING_DATABASE_URL=postgresql://…/warexx_billing_test python lead-api/payments_test.py

Covers: checkout; invalid plan and amount tampering; missing configuration;
HDFC rejecting the session; a timeout (and the order found anyway); success
verified server to server; failure; pending; the customer cancelling; duplicate
and out-of-order notifications; a wrong order reference; amount and currency
mismatches; status lookups without, or with someone else's, order pass; one
customer's account and billing kept from another; activation exactly once;
refunds (success, refusal, too much, twice, unconfirmed); an expired
subscription refused by the server and renewed; webhook authentication; the
return URL's signature.
"""
import base64
import email
import glob
import hashlib
import hmac
import io
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # ₹ on a Windows console
TMP = tempfile.mkdtemp(prefix="warexx-pay-")
OUTBOX = os.path.join(TMP, "mail")
PG = os.environ.get("BILLING_DATABASE_URL", "")
ENV = {"DEMO_TRIAL_SECRET": "test-secret-for-payments-0123456789abcdef", "LEAD_MAIL_BACKEND": "console",
       "LEAD_MAIL_DIR": OUTBOX, "LEADS_SQLITE_PATH": os.path.join(TMP, "leads.db"),
       "BILLING_SQLITE_PATH": os.path.join(TMP, "billing.db"), "LEAD_NOTIFY_TO": "sales@example.com",
       "DEMO_EMAIL_PASSWORD": "1",
       "HDFC_ENV": "production", "HDFC_MERCHANT_ID": "TESTMID", "HDFC_API_KEY": "test-api-key-xyz",
       "HDFC_RESPONSE_KEY": "test-response-key", "HDFC_PAYMENT_PAGE_CLIENT_ID": "TESTMID",
       "HDFC_RETURN_URL": "https://shop.example.test/api/payment/return",
       "HDFC_WEBHOOK_USERNAME": "hook", "HDFC_WEBHOOK_PASSWORD": "hook-pass-123", "BILLING_GST_PERCENT": "18"}
os.environ.update(ENV)
for k in ("ADMIN_PASSWORD", "POSTGRES_URL", "DATABASE_URL", "LEADS_DATABASE_URL", "HDFC_BASE_URL", "BILLING_GRACE_DAYS",
          "BILLING_SANDBOX_AMOUNT", "VERCEL"):
    os.environ.pop(k, None)
os.environ["ADMIN_PASSWORD"] = "admin-pass-for-tests"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import billing  # noqa: E402
import hdfc  # noqa: E402
import http_handler  # noqa: E402
import payments  # noqa: E402

bad = 0


def ok(cond, what):
    global bad
    print(f"  {'ok  ' if cond else 'FAIL'}  {what}")
    if not cond:
        bad += 1


# ---- a fake HDFC SmartGateway ----------------------------------------------------------

class Fake:
    def __init__(self):
        self.orders, self.calls, self.mode = {}, [], {}

    def __call__(self, req, timeout=None):
        u = urlparse(req.full_url)
        h = {k.lower(): v for k, v in req.header_items()}
        body = req.data or b""
        self.calls.append((req.get_method(), u.path, h))
        assert u.scheme == "https" and u.hostname == "smartgateway.hdfc.bank.in", u.hostname   # the documented URL
        if h.get("authorization") != "Basic " + base64.b64encode(b"test-api-key-xyz").decode() \
                or h.get("x-merchantid") != "TESTMID" or h.get("x-resellerid") != "hdfc_reseller" or not h.get("x-customerid"):
            return self.answer(req, 401, {"status": "error", "error_code": "access_denied"})
        path = u.path
        if path == "/session" and req.get_method() == "POST":
            m = self.mode.pop("session", "ok")
            b = json.loads(body)
            if m == "timeout":
                raise socket.timeout("timed out")
            if m == "reject":
                return self.answer(req, 400, {"status": "Bad Request", "error_code": "Mandatory fields are missing"})
            for k in ("order_id", "amount", "customer_id", "customer_email", "customer_phone", "payment_page_client_id",
                      "action", "return_url"):
                if not b.get(k):
                    return self.answer(req, 400, {"status": "Bad Request", "error_code": "missing " + k})
            self.orders[b["order_id"]] = {"order_id": b["order_id"], "amount": float(b["amount"]), "currency": b.get("currency", "INR"),
                                          "status": "NEW", "status_id": 10, "customer_id": b["customer_id"], "refunds": [],
                                          "amount_refunded": 0, "id": "ordeh_" + b["order_id"].lower(), "session_body": b}
            link = f"https://smartgateway.hdfc.bank.in/orders/ordeh_{b['order_id'].lower()}/payment-page"
            if m == "timeout_created":
                self.orders[b["order_id"]]["payment_links"] = {"web": link}
                raise socket.timeout("timed out")
            return self.answer(req, 200, {"status": "NEW", "id": "ordeh_" + b["order_id"].lower(), "order_id": b["order_id"],
                                          "payment_links": {"web": link, "expiry": "2099-01-01T00:00:00Z"},
                                          "sdk_payload": {"payload": {"clientAuthToken": "tkn_secret"}}})
        if path.startswith("/orders/") and path.endswith("/refunds") and req.get_method() == "POST":
            oid = path.split("/")[2]
            f = dict(parse_qsl(body.decode()))
            o = self.orders.get(oid)
            m = self.mode.pop("refund", "ok")
            if m == "timeout":
                raise socket.timeout("timed out")
            if m == "reject" or not o or o["status"] != "CHARGED":
                return self.answer(req, 400, {"status": "Bad Request", "error_code": "refund_not_allowed"})
            if any(r["unique_request_id"] == f["unique_request_id"] for r in o["refunds"]):
                return self.answer(req, 400, {"status": "Bad Request", "error_code": "duplicate_request"})
            o["refunds"].append({"unique_request_id": f["unique_request_id"], "amount": float(f["amount"]), "status": "PENDING",
                                 "ref": "rfnd_" + f["unique_request_id"].lower(), "sent_to_gateway": False})
            return self.answer(req, 200, self.view(o))
        if path.startswith("/orders/") and req.get_method() == "GET":
            if self.mode.get("status_5xx"):
                self.mode["status_5xx"] -= 1
                return self.answer(req, 503, {"status": "error", "error_code": "Internal server Error"})
            o = self.orders.get(path.split("/")[2])
            if not o:
                return self.answer(req, 404, {"status": "NOT_FOUND", "error_code": "Order not found"})
            return self.answer(req, 200, self.view(o))
        return self.answer(req, 404, {"status": "error"})

    def view(self, o):
        v = {k: o[k] for k in ("order_id", "amount", "currency", "status", "status_id", "customer_id", "id", "amount_refunded")}
        v.update({"refunds": [dict(r) for r in o["refunds"]], "txn_id": f"TESTMID-{o['order_id']}-1",
                  "payment_method_type": "CARD", "payment_method": "VISA", "payment_links": o.get("payment_links", {}),
                  "card": {"card_isin": "401200", "last_four_digits": "1097"}, "customer_email": "x@example.com"})
        v.update(o.get("override", {}))
        return v

    def answer(self, req, status, body):
        raw = json.dumps(body).encode()
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(raw))
        return _Resp(raw, status)

    def set(self, ref, status, **kw):
        self.orders[ref].update(status=status, status_id={"CHARGED": 21, "PENDING_VBV": 23, "AUTHORIZATION_FAILED": 27,
                                                           "NEW": 10, "AUTO_REFUNDED": 36}.get(status, 0), **kw)


class _Resp:
    def __init__(self, raw, status):
        self.raw, self.status = raw, status

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


fake = Fake()
hdfc._opener = fake
hdfc.time.sleep = lambda s: None            # the status API's retry pauses, skipped


class H(http_handler.LeadHandler):
    send_in_background = False

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def post(path, body, ip="10.9.0.1", headers=None, raw=None):
    h = {"Content-Type": "application/json", "X-Forwarded-For": ip, **(headers or {})}
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=raw if raw is not None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            data = r.read()
            return r.status, (json.loads(data or b"{}") if "json" in (r.headers.get("Content-Type") or "") else data.decode())
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data or b"{}")
        except ValueError:
            return e.code, data.decode()


def get(path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}") as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def sub(body, **kw):
    return post("/api/subscribe", body, **kw)


def rows(sql, params=()):
    return billing.read(sql, params)


def mails():
    out = []
    for f in sorted(glob.glob(os.path.join(OUTBOX, "*.eml"))):
        with open(f, "rb") as fh:
            m = email.message_from_bytes(fh.read())
        out.append({"to": m["To"], "subject": m["Subject"]})
    return out


def signed(params, key="test-response-key"):
    """A return URL signed the documented way (Resources → HMAC signature)."""
    pairs = sorted((quote_plus(k), quote_plus(v)) for k, v in params.items())
    msg = quote_plus("&".join(f"{k}={v}" for k, v in pairs))
    sig = base64.b64encode(hmac.new(key.encode(), msg.encode(), hashlib.sha256).digest()).decode()
    return {**params, "signature": sig, "signature_algorithm": "HMAC-SHA256"}


def ret(ref, status="CHARGED", sign=True):
    p = {"order_id": ref, "status": status, "status_id": "21"}
    return get("/api/payment/return?" + urlencode(signed(p) if sign else p))


def hook(evt, user="hook", pw="hook-pass-123"):
    auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    return post("/api/payment/webhook", None, headers={"Authorization": auth}, raw=json.dumps(evt).encode())


def details(**kw):
    d = {"action": "checkout", "name": "Asha Rao", "email": "asha@example.com", "phone": "+91 98765 43210",
         "company": "Rao Textiles", "gstin": "", "address": "12 Mill Road", "city": "Coimbatore", "state": "Tamil Nadu",
         "postcode": "641001", "country": "India", "plan": "monthly", "terms_accepted": True, "terms_version": "2026-10"}
    d.update(kw)
    return d


print(f"database: {billing.kind()}" + (" (" + PG.split("@")[-1] + ")" if PG else ""))
billing.reset()

print("configuration")
code, c = sub({"action": "config"})
ok(code == 200 and c["enabled"] and c["plans"]["monthly"]["total"] == "35398.82" and c["plans"]["annual"]["total"] == "388220.00",
   "plans come from the server, with 18% GST (₹29,999 → ₹35,398.82; ₹3,29,000 → ₹3,88,220)")
ok("test-api-key" not in json.dumps(c) and "hook-pass" not in json.dumps(c), "no setting's value is in the answer")
key = os.environ.pop("HDFC_API_KEY")
code, r = sub(details())
ok(code == 503 and r["detail"] == "payments_not_configured", "a missing HDFC setting: checkout refused (503), nothing created")
ok("HDFC_API_KEY" in payments.missing(), "…and the missing setting is named for the logs (names only)")
os.environ["HDFC_API_KEY"] = key
os.environ["HDFC_BASE_URL"] = "https://evil.example.com"
ok("HDFC_BASE_URL (not the documented URL for HDFC_ENV=production)" in payments.missing(), "a base URL other than HDFC's documented one is refused")
os.environ.pop("HDFC_BASE_URL")
code, r = post("/api/subscribe", None, raw=b"action=checkout", headers={"Content-Type": "application/x-www-form-urlencoded"})
ok(code == 415, "a cross-site form post is refused (JSON only)")

print("checkout")
code, r = sub(details(plan="gold"))
ok(code == 422 and r["fields"].get("plan") == "invalid", "an unknown plan is refused")
code, r = sub(details(terms_accepted=False))
ok(code == 422 and "terms" in r["fields"], "the terms must be accepted")
code, a = sub(details(amount="1.00", price="₹1", total="1", tenant="other-co", currency="USD"))
ok(code == 200 and urlparse(a["payment_url"]).hostname == "smartgateway.hdfc.bank.in", "checkout answers HDFC's payment page")
sent = fake.orders[a["order_ref"]]["session_body"]
ok(sent["amount"] == "35398.82" and sent["currency"] == "INR", "the amount sent to HDFC is the server's (₹35,398.82, INR) — not the browser's")
ok(sent["action"] == "paymentPage" and sent["payment_page_client_id"] == "TESTMID" and sent["customer_phone"] == "9876543210"
   and sent["return_url"] == "https://shop.example.test/api/payment/return" and len(a["order_ref"]) < 21 and a["order_ref"].isalnum(),
   "the Session API request as documented (order id < 21 alphanumerics, 10-digit phone, return URL)")
o = rows("SELECT * FROM billing_orders WHERE order_ref = %s", (a["order_ref"],))[0]
ok(o["status"] == "pending" and billing.money(o["amount"]) == billing.money("35398.82") and o["environment"] == "production",
   "a pending order is kept, with the server's amount")
cu = rows("SELECT * FROM billing_customers WHERE email = 'asha@example.com'")[0]
ok(cu["tenant_slug"] == "rao-textiles" and cu["pw_hash"] is None, "the customer, mapped to a workspace (rao-textiles)")
ok("tkn_secret" not in json.dumps(rows("SELECT payload FROM billing_gateway_events")), "the payment page's client token is not kept")
code, a2 = sub(details())
ok(code == 200 and a2["order_ref"] == a["order_ref"] and a2.get("reused"), "a double click: the same order, not a second one")

print("status lookups")
code, r = sub({"action": "status"})
ok(code == 401, "no order pass: refused")
code, r = sub({"action": "status", "order": a["token"][:-3] + "abc"})
ok(code == 401, "an altered order pass: refused")
import trial  # noqa: E402
other = trial.sign({"k": "po", "r": a["order_ref"], "e": "mallory@example.com", "x": 9999999999})
code, r = sub({"action": "status", "order": other})
ok(code == 404, "a pass for someone else's email cannot read this order")
code, r = sub({"action": "status", "order": a["token"]})
ok(code == 200 and r["status"] == "pending" and r["amount"] == "35398.82", "the customer's own pass: pending")

print("pending, then paid")
fake.set(a["order_ref"], "PENDING_VBV")
code, page = ret(a["order_ref"], "PENDING_VBV")
ok(code == 200 and "/pay/?order=" + a["order_ref"] in page and "Location" not in page, "the return URL answers 200 and opens /pay/?order=… (no redirect)")
ok(sub({"action": "status", "order": a["token"]})[1]["status"] == "pending", "PENDING_VBV: pending")
ok(rows("SELECT signature_valid FROM billing_gateway_events WHERE source = 'return'")[0]["signature_valid"] in (True, 1),
   "the return's signature verified (HMAC-SHA256, the response key)")
fake.set(a["order_ref"], "CHARGED")
code, r = hook({"id": "evt_1", "event_name": "ORDER_SUCCEEDED", "content": {"order": {"order_id": a["order_ref"], "status": "CHARGED"}}})
ok(code == 200, "the webhook is accepted")
o = rows("SELECT * FROM billing_orders WHERE order_ref = %s", (a["order_ref"],))[0]
s = rows("SELECT * FROM billing_subscriptions WHERE customer_id = %s", (cu["id"],))[0]
ok(o["status"] == "paid" and o["activated_at"] and s["status"] == "active", "verified with the Order Status API: paid, subscription active")
end1 = s["current_period_end"]
code, r = hook({"id": "evt_1", "event_name": "ORDER_SUCCEEDED", "content": {"order": {"order_id": a["order_ref"]}}})
ok(code == 200 and r.get("duplicate"), "the same webhook again: acknowledged, not processed twice")
ret(a["order_ref"]); ret(a["order_ref"]); sub({"action": "status", "order": a["token"]})
hook({"id": "evt_2", "event_name": "TXN_CHARGED", "content": {"order": {"order_id": a["order_ref"]}}})
s = rows("SELECT * FROM billing_subscriptions WHERE customer_id = %s", (cu["id"],))[0]
ok(len(rows("SELECT * FROM billing_invoices")) == 1 and s["current_period_end"] == end1, "returns, polls and webhooks again: one invoice, one period")
fake.set(a["order_ref"], "PENDING_VBV")
sub({"action": "status", "order": a["token"]}); payments.reconcile(a["order_ref"])
ok(rows("SELECT status FROM billing_orders WHERE order_ref = %s", (a["order_ref"],))[0]["status"] == "paid",
   "an older, out-of-order answer cannot undo a payment")
fake.set(a["order_ref"], "CHARGED")
code, r = sub({"action": "status", "order": a["token"]})
ok(r["status"] == "paid" and r["invoice_no"] and r["account"] == "needed" and r["period_end"], "the result page: paid, receipt number, account to create")
ok(any(m["to"] == "asha@example.com" and "receipt" in m["subject"].lower() for m in mails()), "the receipt is emailed")

print("the account")
code, r = sub({"action": "account", "order": a["token"], "password": "short"})
ok(code == 422, "a short password is refused")
code, acc = sub({"action": "account", "order": a["token"], "password": "asha-strong-pass"})
ok(code == 200 and acc.get("session") and "ref" not in acc, "the account: signed in")
code, r = sub({"action": "account", "order": a["token"], "password": "another-pass-123"})
ok(code == 409 and r["detail"] == "account_exists", "…once")
code, v = post("/api/trial", {"action": "verify", "session": acc["session"]})
ok(code == 200 and v.get("subscriber"), "the session opens WAREXX")
code, si = post("/api/trial", {"action": "signin", "email": "asha@example.com", "password": "asha-strong-pass"})
ok(code == 200 and si.get("session"), "email + password sign in on another device")
code, h = sub({"action": "billing", "session": acc["session"]})
ok(code == 200 and h["subscription"]["status"] == "active" and len(h["orders"]) == 1 and h["orders"][0]["invoice_no"], "billing history: own subscription and payments")

print("another customer")
code, r = sub(details())
ok(code == 409 and r["detail"] == "sign_in_required", "paying for an email that has an account needs its sign-in")
code, b = sub(details(email="bala@example.com", company="Bala Mills", name="Bala K"))
ok(code == 200, "a second customer checks out")
code, r = sub(details(session=b["token"]))
ok(code == 409, "…and cannot pay for (or change) the first customer's account with their own pass")
code, r = sub({"action": "status", "order": trial.sign({"k": "po", "r": a["order_ref"], "e": "bala@example.com", "x": 9999999999})})
ok(code == 404, "…nor read the first customer's order")
code, r = sub({"action": "billing", "session": b["token"]})
ok(code == 401, "…nor their billing history (an order pass is not a session)")

print("the profile section (WAREXX → My profile → Subscription & billing)")
code, r = sub({"action": "account_info"})
ok(code == 401, "no session: refused")
code, i = sub({"action": "account_info", "session": acc["session"]})
ok(code == 200 and i["kind"] == "subscriber" and i["details"]["name"] == "Asha Rao" and i["subscription"]["status"] == "active"
   and i["orders"][0]["invoice_no"] and "pw_hash" not in json.dumps(i), "the account, subscription and payments (no password hash)")
code, r = sub({"action": "account_info", "session": trial.sign({"k": "s", "e": "visitor@example.com", "s": 0, "x": 9999999999})})
ok(code == 200 and r["kind"] == "trial", "a trial visitor's session: a trial, nothing of anyone's billing")
det = {**i["details"], "phone": "123"}
code, r = sub({"action": "update_details", "session": acc["session"], **det})
ok(code == 422 and "phone" in r["fields"], "billing details are checked")
code, r = sub({"action": "update_details", "session": acc["session"], **i["details"], "gstin": "33aadce6591n1z7", "city": "Tiruppur"})
ok(code == 200 and sub({"action": "account_info", "session": acc["session"]})[1]["details"]["gstin"] == "33AADCE6591N1Z7",
   "billing details saved (GSTIN upper-cased)")
code, r = sub({"action": "change_password", "session": acc["session"], "current": "nope-nope", "password": "a-new-password-1"})
ok(code == 401, "changing the password needs the current one")
code, r = sub({"action": "change_password", "session": acc["session"], "current": "asha-strong-pass", "password": "a-new-password-1"})
ok(code == 200 and post("/api/trial", {"action": "signin", "email": "asha@example.com", "password": "a-new-password-1"})[0] == 200
   and post("/api/trial", {"action": "signin", "email": "asha@example.com", "password": "asha-strong-pass"})[0] == 401,
   "the new password signs in, the old one no longer does")
sub({"action": "change_password", "session": acc["session"], "current": "a-new-password-1", "password": "asha-strong-pass"})
code, r = sub({"action": "receipt", "session": acc["session"], "order_ref": a["order_ref"]})
ok(code == 200 and r["receipt"]["invoice_no"] and r["receipt"]["total"] == "35398.82", "the customer's own receipt")
code, r = sub({"action": "receipt", "session": acc["session"], "order_ref": b["order_ref"]})
ok(code == 404, "…never someone else's")
code, r = sub({"action": "cancel_subscription", "session": acc["session"], "reason": "testing"})
ok(code == 200 and r["ends_at"], "cancel: accepted, access until the end of the paid period")
i = sub({"action": "account_info", "session": acc["session"]})[1]
ok(i["subscription"]["cancel_at_period_end"] and i["subscription"]["status"] == "active"
   and post("/api/trial", {"action": "verify", "session": acc["session"]})[0] == 200, "…still active until then")
ok(any("cancelled" in (m["subject"] or "").lower() and m["to"] == "asha@example.com" for m in mails()), "…and confirmed by email")
code, r = sub({"action": "cancel_subscription", "session": acc["session"]})
ok(code == 200 and r.get("already"), "cancelling twice changes nothing")
code, r = sub({"action": "resume_subscription", "session": acc["session"]})
ok(code == 200 and not sub({"action": "account_info", "session": acc["session"]})[1]["subscription"]["cancel_at_period_end"],
   "keep my subscription: the cancellation is withdrawn")
ok(any(x["action"] == "cancel_requested" for x in rows("SELECT action FROM billing_audit WHERE customer_id = %s", (cu["id"],))),
   "cancel and resume are in the audit trail")

print("failed, then retried")
fake.set(b["order_ref"], "AUTHORIZATION_FAILED", override={"bank_error_message": "Insufficient funds"})
ret(b["order_ref"], "AUTHORIZATION_FAILED")
code, r = sub({"action": "status", "order": b["token"]})
ok(r["status"] == "failed" and r["can_retry"] and r["failure_reason"] == "Insufficient funds", "failed, with the bank's reason and a retry")
ok(rows("SELECT s.status FROM billing_subscriptions s JOIN billing_customers c ON c.id = s.customer_id WHERE c.email = 'bala@example.com'")[0]["status"]
   == "payment_failed", "the subscription: payment failed, not active")
code, b2 = sub({"action": "retry", "order": b["token"]})
ok(code == 200 and b2["order_ref"] != b["order_ref"], "retry: a new order (an order id is never paid twice)")

print("cancelled")
code, r = ret(b2["order_ref"], "NEW", sign=False)
ok(sub({"action": "status", "order": b2["token"]})[1]["status"] == "cancelled", "back without paying: cancelled (no signature needed: HDFC is asked)")

print("wrong references and amounts")
code, page = get("/api/payment/return?order_id=NOTANORDER123&status=CHARGED")
ok(code == 200 and "/pay/?order=" not in page, "an unknown order id on the return: nothing happens")
code, r = hook({"id": "evt_x", "event_name": "ORDER_SUCCEEDED", "content": {"order": {"order_id": "NOTANORDER123"}}})
ok(code == 200, "a webhook for an unknown order: acknowledged, ignored")
code, c3 = sub(details(email="chen@example.com", company="Chen Co", name="Chen L"))
fake.set(c3["order_ref"], "CHARGED", override={"amount": 1.0})
ret(c3["order_ref"])
o = rows("SELECT status, activated_at FROM billing_orders WHERE order_ref = %s", (c3["order_ref"],))[0]
ok(o["status"] == "unknown" and not o["activated_at"], "HDFC reports a different amount: not activated, held for review")
fake.set(c3["order_ref"], "CHARGED", override={"currency": "USD"})
payments.reconcile(c3["order_ref"])
ok(not rows("SELECT activated_at FROM billing_orders WHERE order_ref = %s", (c3["order_ref"],))[0]["activated_at"], "a different currency: not activated")
fake.set(c3["order_ref"], "CHARGED", override={"order_id": "SOMEOTHERORDER1"})
payments.reconcile(c3["order_ref"])
ok(not rows("SELECT activated_at FROM billing_orders WHERE order_ref = %s", (c3["order_ref"],))[0]["activated_at"], "a different order id in the answer: not activated")
ok(any(x["action"] == "gateway_mismatch" for x in rows("SELECT action FROM billing_audit WHERE order_ref = %s", (c3["order_ref"],))),
   "mismatches are in the audit trail")
code, r = ret(c3["order_ref"], "CHARGED", sign=False)
p = signed({"order_id": c3["order_ref"], "status": "CHARGED", "status_id": "21"}); p["status"] = "AUTHORIZATION_FAILED"
get("/api/payment/return?" + urlencode(p))
ok(rows("SELECT signature_valid FROM billing_gateway_events WHERE source = 'return' AND order_ref = %s ORDER BY id DESC", (c3["order_ref"],))[0]["signature_valid"] in (False, 0),
   "an altered return is recorded as an invalid signature")

print("HDFC unavailable or refusing")
fake.mode["session"] = "reject"
code, r = sub(details(email="dev@example.com", company="Dev Co", name="Dev P"))
ok(code == 502 and r["detail"] == "gateway_rejected", "HDFC rejects the session: a clear error, nothing charged")
ok(rows("SELECT status FROM billing_orders o JOIN billing_customers c ON c.id = o.customer_id WHERE c.email = 'dev@example.com'")[0]["status"] == "failed",
   "…the order is kept as failed")
fake.mode["session"] = "timeout"
code, r = sub(details(email="eve@example.com", company="Eve Co", name="Eve S"))
ok(code == 503 and r["detail"] == "gateway_unavailable", "a timeout, and no order at HDFC: unavailable, try again")
fake.mode["session"] = "timeout_created"
code, r = sub(details(email="fay@example.com", company="Fay Co", name="Fay M"))
ok(code == 200 and r["payment_url"].startswith("https://smartgateway.hdfc.bank.in/"), "a timeout after HDFC made the order: found with the Order Status API, carried on")
fake.mode["status_5xx"] = 2
fake.set(r["order_ref"], "CHARGED")
row = payments.reconcile(r["order_ref"])
ok(row["status"] == "paid", "the Order Status API failing twice (5xx), then answering: retried, paid")

print("a cancelled subscription ends with its period")
fay = trial.sign({"k": "s", "e": "fay@example.com", "s": 0, "x": 9999999999, "n": "x", "p": "sub"})
code, r = sub({"action": "cancel_subscription", "session": fay})
ok(code == 200, "cancelled")
with billing.tx() as t:
    t.run("UPDATE billing_subscriptions SET current_period_start = %s, current_period_end = %s "
          "WHERE customer_id = (SELECT id FROM billing_customers WHERE email = 'fay@example.com')",
          (billing.now() - billing.dt.timedelta(days=31), billing.now() - billing.dt.timedelta(minutes=1)))
ok(not payments.subscriber_access("fay@example.com")
   and rows("SELECT s.status FROM billing_subscriptions s JOIN billing_customers c ON c.id = s.customer_id WHERE c.email = 'fay@example.com'")[0]["status"] == "cancelled",
   "after the period: cancelled, no access (no grace for a cancellation)")
code, r = sub({"action": "resume_subscription", "session": fay})
ok(code == 409, "too late to undo — pay again instead")

print("refunds (the team only)")
code, login = post("/api/admin", {"action": "login", "password": "admin-pass-for-tests"})
tok = login["token"]
code, r = post("/api/admin", {"action": "payment_refund", "order_ref": a["order_ref"], "amount": "100.00"})
ok(code == 401, "without the admin sign-in: refused")
code, r = sub({"action": "refund", "order": a["token"], "amount": "100"})
ok(code == 400, "there is no customer refund action")
adm = lambda body: post("/api/admin", {**body, "token": tok})  # noqa: E731
code, r = adm({"action": "payment_refund", "order_ref": b2["order_ref"], "amount": "10"})
ok(code == 409, "an unpaid order cannot be refunded")
code, r = adm({"action": "payment_refund", "order_ref": a["order_ref"], "amount": "40000"})
ok(code == 422, "more than was paid: refused")
code, r = adm({"action": "payment_refund", "order_ref": a["order_ref"], "amount": "1000.00", "reason": "goodwill"})
ok(code == 200 and r["refund"]["status"] == "pending", "a partial refund: asked of HDFC, pending")
code, r = adm({"action": "payment_refund", "order_ref": a["order_ref"], "amount": "34398.83"})
ok(code == 422, "with one pending, more than what is left: refused (no double refund)")
fake.mode["refund"] = "timeout"
code, r = adm({"action": "payment_refund", "order_ref": a["order_ref"], "amount": "500.00"})
ok(code == 503 and r["detail"] == "refund_unconfirmed", "HDFC does not answer: unconfirmed, kept as requested")
code, r = adm({"action": "payment_refund", "order_ref": a["order_ref"], "amount": "33898.83"})
ok(code == 422, "…and still counted: no refund beyond what was paid")
fake.mode["refund"] = "reject"
code, r = adm({"action": "payment_refund", "order_ref": a["order_ref"], "amount": "10.00"})
ok(code == 502 and rows("SELECT status FROM billing_refunds ORDER BY id DESC")[0]["status"] == "error", "HDFC refuses: recorded as an error")
fake.orders[a["order_ref"]]["refunds"][0]["status"] = "SUCCESS"
fake.orders[a["order_ref"]]["amount_refunded"] = 1000.0
code, r = adm({"action": "payment_check", "order_ref": a["order_ref"]})
ok(r["status"] == "partially_refunded" and rows("SELECT status FROM billing_refunds ORDER BY id")[0]["status"] == "success",
   "HDFC settles it: refund success, order partially refunded, still active")
code, lst = adm({"action": "payments"})
ok(code == 200 and lst["total"] >= 5 and "test-api-key" not in json.dumps(lst) and "pw_hash" not in json.dumps(lst),
   "the team's payments list (no keys, no password hashes)")

print("expiry and renewal")
cid = cu["id"]
with billing.tx() as t:
    t.run("UPDATE billing_subscriptions SET current_period_start = %s, current_period_end = %s WHERE customer_id = %s",
          (billing.now() - billing.dt.timedelta(days=40), billing.now() - billing.dt.timedelta(days=5), cid))
code, v = post("/api/trial", {"action": "verify", "session": acc["session"]})
ok(code == 403 and v["detail"] == "expired", "the period is over: the server refuses the session (not just an overlay)")
code, si = post("/api/trial", {"action": "signin", "email": "asha@example.com", "password": "asha-strong-pass"})
ok(code == 403, "…and the sign-in")
code, r = sub(details())
ok(code == 409, "renewing without the account's session: sign in first")
code, rn = sub(details(session=acc["session"]))
ok(code == 200 and rows("SELECT kind FROM billing_orders WHERE order_ref = %s", (rn["order_ref"],))[0]["kind"] == "new",
   "with the account's own session it can pay: a new period (the old one has expired)")
fake.set(rn["order_ref"], "CHARGED")
ret(rn["order_ref"])
s = rows("SELECT * FROM billing_subscriptions WHERE customer_id = %s", (cid,))[0]
ok(s["status"] == "active" and billing.parse(s["current_period_end"]) > billing.now(), "renewed: active again, from today")
code, v = post("/api/trial", {"action": "verify", "session": acc["session"]})
ok(code == 200, "the session opens WAREXX again")
code, rn2 = sub({**details(plan="annual"), "session": trial.sign({"k": "s", "e": "asha@example.com", "s": 0, "x": 9999999999, "n": "cust:1", "p": "sub"})})
fake.set(rn2["order_ref"], "CHARGED", amount=388220.0)
payments.reconcile(rn2["order_ref"])
s2 = rows("SELECT * FROM billing_subscriptions WHERE customer_id = %s", (cid,))[0]
ok(rows("SELECT kind FROM billing_orders WHERE order_ref = %s", (rn2["order_ref"],))[0]["kind"] == "renewal"
   and billing.parse(s2["current_period_start"]) == billing.parse(s["current_period_end"]),
   "an early renewal (annual) starts when the running period ends")

print("a full refund ends the period it paid for")
fake.orders[rn2["order_ref"]]["refunds"].append({"unique_request_id": "RFTEST00000001", "amount": 388220.0, "status": "SUCCESS"})
fake.orders[rn2["order_ref"]]["amount_refunded"] = 388220.0
payments.reconcile(rn2["order_ref"])
ok(rows("SELECT status FROM billing_subscriptions WHERE customer_id = %s", (cid,))[0]["status"] == "cancelled",
   "refunded in full: the subscription is cancelled, access ends")

print("webhook authentication")
ok(hook({"id": "evt_9", "content": {}}, pw="wrong")[0] == 401, "a wrong webhook password: 401")
code, r = post("/api/payment/webhook", None, raw=b"{}")
ok(code == 401, "no Authorization header: 401")
os.environ.pop("HDFC_WEBHOOK_PASSWORD")
ok(hook({"id": "evt_9", "content": {}})[0] == 503, "webhook credentials not configured: 503 (HDFC retries later)")
os.environ["HDFC_WEBHOOK_PASSWORD"] = "hook-pass-123"

print("the sandbox")
os.environ.update({"HDFC_ENV": "sandbox", "HDFC_PAYMENT_PAGE_CLIENT_ID": "hdfcmaster", "HDFC_RETURN_URL": "http://localhost:8030/api/payment/return"})
hdfc_base = hdfc.BASE_URLS["sandbox"]
fake_host = "smartgateway.hdfcuat.bank.in"
orig = fake.__call__


def sandbox_call(req, timeout=None):
    req.full_url = req.full_url.replace(hdfc_base, "https://smartgateway.hdfc.bank.in")
    r = orig(req, timeout)
    if isinstance(r, _Resp):
        r.raw = r.raw.replace(b"smartgateway.hdfc.bank.in", fake_host.encode())
    return r


hdfc._opener = sandbox_call
code, c = sub({"action": "config"})
ok(c["environment"] == "sandbox" and c["plans"]["monthly"]["charged"] == "1.00", "sandbox: the page is told a ₹1.00 test charge")
code, r = sub(details(email="sandy@example.com", company="Sandy Co", name="Sandy B"))
ok(code == 200 and fake.orders[r["order_ref"]]["session_body"]["amount"] == "1.00"
   and fake.orders[r["order_ref"]]["session_body"]["payment_page_client_id"] == "hdfcmaster", "sandbox: ₹1.00 sent, client id hdfcmaster")
o = rows("SELECT environment, list_total, amount FROM billing_orders WHERE order_ref = %s", (r["order_ref"],))[0]
ok(o["environment"] == "sandbox" and billing.money(o["list_total"]) == billing.money("35398.82"), "…and recorded as a sandbox order (the real total kept beside it)")
os.environ["HDFC_RETURN_URL"] = "http://127.0.0.1:8030/api/payment/return"
ok(any("HDFC_RETURN_URL" in m for m in payments.missing()), "a return URL with an IP address is refused")

srv.shutdown()
print(f"\n{bad} FAILED" if bad else "\nall passing")
sys.exit(1 if bad else 0)
