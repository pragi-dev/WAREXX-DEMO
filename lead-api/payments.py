"""Subscriptions paid through HDFC SmartGateway: checkout, verification,
activation, the customer's billing, refunds. The HTTP side is http_handler.py;
the gateway calls are hdfc.py; the records are billing.py.

    Subscribe (/pay) → billing details → terms → checkout()
        → an order here (pending) → HDFC Session API → the customer on HDFC's payment page
    HDFC → /api/payment/return (the customer's browser)  → handle_return() → reconcile()
    HDFC → /api/payment/webhook (server to server)       → webhook()       → reconcile()
    the /pay result page, polling                        → status()        → reconcile() while pending

reconcile() is the only place an order changes status, and it trusts only the
Order Status API (server to server, authenticated): never the return URL's
parameters, a webhook's body or the browser. The order id, the amount and the
currency HDFC reports must match the order kept here, or nothing is activated.
It is idempotent: the order row is locked, and a subscription period is added
once per order (activated_at), however many returns, webhooks and polls arrive,
in whatever order. A paid order never goes back to pending or failed.

Prices are the server's (PLANS — the landing page's pricing), plus GST at
BILLING_GST_PERCENT; nothing the browser sends sets an amount, a tenant or a
period. Payments are one-time: each period is paid for explicitly (renewal);
recurring mandates are a separate HDFC product, not used here.
"""
import calendar
import datetime as dt
import html
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import billing
import hdfc
import lead_mail
import trial

#: The plans on sale, as WAREXX_CONFIG.pricing on the landing page (₹, before GST).
PLANS = {
    "monthly": {"name": "Monthly", "unit": "month", "count": 1, "price": Decimal("29999.00"), "period": "per month"},
    "annual": {"name": "Annual", "unit": "year", "count": 1, "price": Decimal("329000.00"), "period": "per year"},
}
CURRENCY = "INR"
ORDER_TOKEN_DAYS = 7
REUSE_PENDING_MINUTES = 10
_EMAIL = re.compile(r"^[A-Za-z0-9!#$%&'*+\-/=?^_.{|}~]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_REF = re.compile(r"^[A-Za-z0-9]{8,20}$")
LIMITS = {"name": 80, "email": 254, "phone": 20, "company": 120, "gstin": 15, "address": 200,
          "city": 80, "state": 80, "postcode": 12, "country": 60}


class PaymentError(Exception):
    def __init__(self, status, detail, **extra):
        super().__init__(detail)
        self.status, self.detail, self.extra = status, detail, extra


# ---- settings ------------------------------------------------------------------------

def gst_percent():
    v = (os.environ.get("BILLING_GST_PERCENT") or "").strip()
    try:
        d = Decimal(v)
    except InvalidOperation:
        return None
    return d if Decimal("0") <= d <= Decimal("100") else None


def grace_days():
    try:
        return max(0, int(os.environ.get("BILLING_GRACE_DAYS") or 0))
    except ValueError:
        return 0


def sandbox_amount():
    """What a sandbox (UAT) order charges, whatever the plan: HDFC's test
    gateway approves only amounts under ₹500. Production always charges the
    plan's own total."""
    try:
        d = billing.money(os.environ.get("BILLING_SANDBOX_AMOUNT") or "1.00")
    except InvalidOperation:
        d = Decimal("1.00")
    return d if Decimal("1.00") <= d < Decimal("500.00") else Decimal("1.00")


def missing():
    """Every setting payments need that is missing or wrong (names only)."""
    out = list(hdfc.missing())
    if gst_percent() is None:
        out.append("BILLING_GST_PERCENT")
    if not trial.configured():
        out.append("DEMO_TRIAL_SECRET")
    if billing.hosted() and not billing.database_url():
        out.append("BILLING_DATABASE_URL")
    return out


def ready():
    m = missing()
    if m:
        print("[payments] not configured — missing: " + ", ".join(m), flush=True)
        raise PaymentError(503, "payments_not_configured")
    return hdfc.config()


def quote(code):
    p = PLANS[code]
    pct = gst_percent() or Decimal("0")
    tax = (p["price"] * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = p["price"] + tax
    env = (os.environ.get("HDFC_ENV") or "").strip().lower()
    charged = sandbox_amount() if env == "sandbox" else total
    return {"code": code, "name": p["name"], "period": p["period"], "base": p["price"], "tax_percent": pct,
            "tax": tax, "total": total, "charged": charged, "currency": CURRENCY, "environment": env}


def public_config():
    """What the /pay page shows: whether paying works here, the plans with their
    GST and total, and the sandbox's test amount. No setting values."""
    enabled = not missing()
    env = (os.environ.get("HDFC_ENV") or "").strip().lower()
    plans = {}
    if gst_percent() is not None:
        for code in PLANS:
            q = quote(code)
            plans[code] = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in q.items()}
    return {"ok": True, "enabled": enabled, "environment": env if enabled else "", "currency": CURRENCY,
            "gst_percent": str(gst_percent()) if gst_percent() is not None else None, "plans": plans}


# ---- abuse limits ---------------------------------------------------------------------

_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def _limit(key, per_hour, now=None):
    now = time.time() if now is None else now
    with _hits_lock:
        q = _hits[key]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= per_hour:
            return max(1, int((q[0] + 3600 - now) // 60) + 1)
        q.append(now)
    return 0


def _checkout_allowed(ip, email):
    wait = _limit("co-ip:" + (ip or "?"), 20) or _limit("co-email:" + email, 10)
    if wait:
        raise PaymentError(429, "too_many_attempts", retry_minutes=wait)


# ---- checkout ---------------------------------------------------------------------------

def _clean(data):
    f = {k: re.sub(r"\s+", " ", str(data.get(k) or "")).strip()[:n] for k, n in LIMITS.items()}
    f["email"], f["gstin"] = f["email"].lower(), f["gstin"].upper()
    bad = {}
    for k in ("name", "company", "address", "city", "state", "country"):
        if not f[k]:
            bad[k] = "required"
    if not _EMAIL.match(f["email"]) or f["email"][0] in "._" or f["email"].split("@")[0][-1] in "._":
        bad["email"] = "invalid"
    if len(hdfc.clean_phone(f["phone"])) < 10:
        bad["phone"] = "invalid"
    if not f["postcode"]:
        bad["postcode"] = "required"
    elif f["country"] == "India" and not re.fullmatch(r"\d{6}", f["postcode"]):
        bad["postcode"] = "invalid"
    if f["gstin"] and not _GSTIN.match(f["gstin"]):
        bad["gstin"] = "invalid"
    plan = str(data.get("plan") or "").lower()
    if plan not in PLANS:
        bad["plan"] = "invalid"
    if data.get("terms_accepted") is not True:
        bad["terms"] = "required"
    if bad:
        raise PaymentError(422, "invalid_fields", fields=bad)
    f["plan"] = plan
    f["terms_version"] = re.sub(r"[^\w.-]", "", str(data.get("terms_version") or ""))[:20] or "unknown"
    return f


def _slug(company, t):
    base = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")[:30].strip("-") or "workspace"
    for i in range(1, 200):
        s = base if i == 1 else f"{base[:26]}-{i}"
        if not t.one("SELECT id FROM billing_customers WHERE tenant_slug = %s", (s,)):
            return s
    return base[:20] + "-" + secrets.token_hex(3)


def _sync_plans(t):
    n = billing.now()
    for code, p in PLANS.items():
        t.run("""INSERT INTO billing_plans (code, name, interval_unit, interval_count, price, currency, active, updated_at)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                 ON CONFLICT (code) DO UPDATE SET name = excluded.name, interval_unit = excluded.interval_unit,
                   interval_count = excluded.interval_count, price = excluded.price, currency = excluded.currency,
                   active = excluded.active, updated_at = excluded.updated_at""",
              (code, p["name"], p["unit"], p["count"], p["price"], CURRENCY, True, n))


def _audit(t, actor, action, order_ref=None, customer_id=None, detail=None):
    t.run("INSERT INTO billing_audit (at, actor, action, order_ref, customer_id, detail) VALUES (%s, %s, %s, %s, %s, %s)",
          (billing.now(), actor, action, order_ref, customer_id, detail or {}))


def _event(t, source, order_ref, gateway_status=None, payload=None, event_id=None, event_name=None, signature_valid=None):
    t.run("""INSERT INTO billing_gateway_events (received_at, source, order_ref, event_id, event_name, gateway_status,
                                                 signature_valid, payload)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
          (billing.now(), source, order_ref, event_id, event_name, gateway_status, signature_valid, payload or {}))


def _order_token(ref, email):
    return trial.sign({"k": "po", "r": ref, "e": email, "x": int(time.time()) + ORDER_TOKEN_DAYS * 86400})


def _session_email(session_token):
    if not session_token:
        return None
    try:
        s = trial.unsign(session_token, "s")
    except trial.TrialError:
        return None
    return s["e"] if s.get("p") == "sub" and time.time() < s["x"] else None


def checkout(data, ip="", session_token=None):
    """A new order for a plan, and HDFC's payment page for it."""
    ready()
    f = _clean(data)
    signed_in = _session_email(session_token)
    with billing.tx() as t:
        _sync_plans(t)
        cust = t.one("SELECT * FROM billing_customers WHERE email = %s FOR UPDATE", (f["email"],))
        if cust and cust["pw_hash"] and signed_in != f["email"]:
            # an account exists for this email: only its holder may pay for (and change) it
            raise PaymentError(409, "sign_in_required")
        _checkout_allowed(ip, f["email"])
        if cust:
            sub = t.one("SELECT * FROM billing_subscriptions WHERE customer_id = %s FOR UPDATE", (cust["id"],))
            reuse = t.one("""SELECT order_ref, payment_link, payment_link_expires_at, amount FROM billing_orders
                             WHERE customer_id = %s AND plan_code = %s AND status = 'pending' AND payment_link IS NOT NULL
                               AND created_at > %s ORDER BY created_at DESC""",
                          (cust["id"], f["plan"], billing.now() - dt.timedelta(minutes=REUSE_PENDING_MINUTES)))
            if reuse and billing.money(reuse["amount"]) == quote(f["plan"])["charged"] and (
                    not reuse["payment_link_expires_at"] or billing.parse(reuse["payment_link_expires_at"]) > billing.now()):
                # the same customer and plan a moment ago (a double click, a back button): the same order
                return {"ok": True, "order_ref": reuse["order_ref"], "payment_url": reuse["payment_link"],
                        "token": _order_token(reuse["order_ref"], f["email"]), "reused": True}
            t.run("""UPDATE billing_customers SET name = %s, phone = %s, company = %s, gstin = %s, address = %s, city = %s,
                       state = %s, postcode = %s, country = %s, updated_at = %s WHERE id = %s""",
                  (f["name"], f["phone"], f["company"], f["gstin"] or None, f["address"], f["city"], f["state"],
                   f["postcode"], f["country"], billing.now(), cust["id"]))
        else:
            n = billing.now()
            cid = t.insert("""INSERT INTO billing_customers (email, gateway_customer_id, tenant_slug, name, phone, company,
                                gstin, address, city, state, postcode, country, created_at, updated_at)
                              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                           (f["email"], "wxc" + secrets.token_hex(8), _slug(f["company"], t), f["name"], f["phone"],
                            f["company"], f["gstin"] or None, f["address"], f["city"], f["state"], f["postcode"],
                            f["country"], n, n))
            cust = t.one("SELECT * FROM billing_customers WHERE id = %s", (cid,))
            sub = None
        if not sub:
            n = billing.now()
            sid = t.insert("""INSERT INTO billing_subscriptions (customer_id, plan_code, status, created_at, updated_at)
                              VALUES (%s, %s, 'payment_pending', %s, %s)""", (cust["id"], f["plan"], n, n))
            sub = t.one("SELECT * FROM billing_subscriptions WHERE id = %s", (sid,))
        sub = _refresh(t, sub)
        kind = "renewal" if sub["status"] in ("active", "grace") else "new"
        ref = _new_order(t, cust, sub, f["plan"], kind, f["terms_version"])
        _audit(t, "customer", "order_created", ref, cust["id"], {"plan": f["plan"], "kind": kind})
    return _open_session(ref, cust, f["plan"])


def _new_order(t, cust, sub, plan, kind, terms_version):
    q = quote(plan)
    ref = "WX" + secrets.token_hex(8).upper()          # 18 characters, alphanumeric, not sequential
    n = billing.now()
    t.run("""INSERT INTO billing_orders (order_ref, customer_id, subscription_id, plan_code, kind, environment,
               list_base, tax_percent, list_tax, list_total, amount, currency, status, terms_version, terms_accepted_at,
               created_at, updated_at)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'created', %s, %s, %s, %s)""",
          (ref, cust["id"], sub["id"], plan, kind, q["environment"], q["base"], q["tax_percent"], q["tax"], q["total"],
           q["charged"], CURRENCY, terms_version, n, n, n))
    return ref


def _open_session(ref, cust, plan):
    """HDFC's Session API for an order kept as 'created'. A timeout is not a
    failure until the Order Status API says the order is not there."""
    q = quote(plan)
    first, _, last = cust["name"].partition(" ")
    desc = f"WAREXX {PLANS[plan]['name']} subscription" + (" - test" if q["environment"] == "sandbox" else "")
    args = (ref, str(q["charged"]), cust["gateway_customer_id"], cust["email"], hdfc.clean_phone(cust["phone"]),
            hdfc.clean_name(first) or "Customer", hdfc.clean_name(last), desc, {"udf1": plan})
    try:
        s = hdfc.create_session(*args)
    except hdfc.GatewayError as e:
        s = None
        if e.kind == "unavailable":
            try:      # did the order reach HDFC before the connection dropped?
                st = hdfc.order_status(ref, cust["gateway_customer_id"], retries=1)
                link = (st.get("payment_links") or {}).get("web") or ""
                if st.get("order_id") == ref and hdfc.link_is_gateway(link):
                    s = {"id": st.get("id") or "", "status": st.get("status") or "", "payment_link": link, "expiry": ""}
            except hdfc.GatewayError:
                pass
        if s is None:
            with billing.tx() as t:
                t.run("UPDATE billing_orders SET status = 'failed', failure_reason = %s, updated_at = %s WHERE order_ref = %s",
                      (("gateway_unavailable" if e.kind == "unavailable" else f"gateway_rejected:{e.code}")[:120],
                       billing.now(), ref))
                _event(t, "session", ref, None, {"error": e.kind, "http_status": e.status, "code": e.code})
                _audit(t, "system", "session_failed", ref, cust["id"], {"kind": e.kind, "code": e.code})
            print(f"[payments] session for {ref} failed: {e.kind} {e.status or ''} {e.code}".rstrip(), flush=True)
            raise PaymentError(503 if e.kind == "unavailable" else 502,
                               "gateway_unavailable" if e.kind == "unavailable" else "gateway_rejected")
    with billing.tx() as t:
        t.run("""UPDATE billing_orders SET status = 'pending', gateway_order_id = %s, gateway_status = %s, payment_link = %s,
                   payment_link_expires_at = %s, updated_at = %s WHERE order_ref = %s AND status = 'created'""",
              (s["id"], s["status"], s["payment_link"], billing.parse(s["expiry"]) if s["expiry"] else None,
               billing.now(), ref))
        _event(t, "session", ref, s["status"], {"id": s["id"], "status": s["status"], "expiry": s["expiry"]})
    return {"ok": True, "order_ref": ref, "payment_url": s["payment_link"], "token": _order_token(ref, cust["email"]),
            "amount": str(q["charged"]), "currency": CURRENCY, "environment": q["environment"]}


# ---- verification ---------------------------------------------------------------------

def _add_interval(start, unit, count):
    months = count * (12 if unit == "year" else 1)
    m = start.month - 1 + months
    y, m = start.year + m // 12, m % 12 + 1
    return start.replace(year=y, month=m, day=min(start.day, calendar.monthrange(y, m)[1]))


def _refresh(t, sub, at=None):
    """A subscription's status as of now: a finished period is grace (if
    BILLING_GRACE_DAYS), then expired. Kept, so every reader sees the same."""
    at = at or billing.now()
    end, grace = billing.parse(sub.get("current_period_end")), billing.parse(sub.get("grace_until"))
    new = sub["status"]
    if sub["status"] in ("active", "grace") and end and at >= end:
        new = "cancelled" if sub.get("cancel_at_period_end") else "grace" if grace and at < grace else "expired"
    if new != sub["status"]:
        t.run("UPDATE billing_subscriptions SET status = %s, updated_at = %s WHERE id = %s", (new, at, sub["id"]))
        _audit(t, "system", "subscription_" + new, None, sub["customer_id"], {"from": sub["status"]})
        sub = {**sub, "status": new}
    return sub


def _activate(t, o, sub):
    """The paid period, once per order: after the current one if it is still
    running (a renewal), else from now."""
    plan = PLANS[o["plan_code"]]
    n = billing.now()
    start = n
    end = billing.parse(sub.get("current_period_end"))
    if sub["status"] in ("active", "grace") and end and end > n:
        start = end
    finish = _add_interval(start, plan["unit"], plan["count"])
    grace = finish + dt.timedelta(days=grace_days()) if grace_days() else None
    t.run("""UPDATE billing_subscriptions SET plan_code = %s, status = 'active', current_period_start = %s,
               current_period_end = %s, grace_until = %s, cancelled_at = NULL, cancel_at_period_end = %s,
               cancel_requested_at = NULL, cancel_reason = NULL, updated_at = %s WHERE id = %s""",
          (o["plan_code"], start, finish, grace, False, n, sub["id"]))
    t.run("""UPDATE billing_orders SET activated_at = %s, paid_at = COALESCE(paid_at, %s), period_start = %s,
               period_end = %s, updated_at = %s WHERE id = %s""", (n, n, start, finish, n, o["id"]))
    cust = t.one("SELECT * FROM billing_customers WHERE id = %s", (o["customer_id"],))
    bill_to = ", ".join(x for x in (cust["company"], cust["address"], cust["city"], cust["state"], cust["postcode"],
                                    cust["country"]) if x)
    t.run("""INSERT INTO billing_invoices (invoice_no, order_id, customer_id, issued_at, base, tax_percent, tax, total,
               amount_charged, currency, environment, gstin, bill_to)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
          (f"WX-{n:%Y%m}-{int(o['id']):06d}", o["id"], o["customer_id"], n, o["list_base"], o["tax_percent"],
           o["list_tax"], o["list_total"], o["amount"], o["currency"], o["environment"], cust["gstin"], bill_to))
    _audit(t, "system", "subscription_activated", o["order_ref"], o["customer_id"],
           {"plan": o["plan_code"], "period_start": billing.iso(start), "period_end": billing.iso(finish)})
    return start, finish


_REFUND_STATUS = {"PENDING": "pending", "SUCCESS": "success", "FAILURE": "failure", "MANUAL_REVIEW": "manual_review"}


def _next_status(o, g, refunded, returned, n):
    amount = billing.money(o["amount"])
    if g in hdfc.PAID:
        return "refunded" if refunded >= amount else "partially_refunded" if refunded > 0 else "paid"
    if o["status"] in ("paid", "partially_refunded", "refunded"):
        return o["status"]                       # paid stays paid: an older or out-of-order answer changes nothing
    if g in hdfc.REFUNDED:
        return "refunded"
    if g in hdfc.FAILED:
        return "failed"
    if g in hdfc.PENDING:
        return "pending"
    if g in hdfc.NOT_STARTED:
        exp = billing.parse(o.get("payment_link_expires_at"))
        if exp and n >= exp:
            return "expired"
        if returned:
            return "cancelled"                   # back from the payment page without paying
        return o["status"] if o["status"] in ("pending", "cancelled") else "pending"
    return "unknown"


def reconcile(ref, source="status", returned=False):
    """The order's status from HDFC's Order Status API, applied (see the module
    docstring). Returns the order row as kept, or None for an unknown order."""
    with billing.tx() as t:
        o = t.one("""SELECT o.*, c.gateway_customer_id FROM billing_orders o
                     JOIN billing_customers c ON c.id = o.customer_id WHERE o.order_ref = %s""", (ref,))
    if not o:
        return None
    if o["status"] == "created":          # the session never opened: there is nothing at HDFC to ask about
        return o
    try:
        resp = hdfc.order_status(ref, o["gateway_customer_id"])
    except hdfc.GatewayError as e:
        print(f"[payments] status check for {ref} failed: {e.kind} {e.status or ''}".rstrip(), flush=True)
        with billing.tx() as t:
            t.run("UPDATE billing_orders SET last_checked_at = %s WHERE order_ref = %s", (billing.now(), ref))
        return {**o, "check_failed": e.kind}
    return _apply(ref, resp, source, returned)


def _apply(ref, resp, source, returned=False):
    activated = False
    with billing.tx() as t:
        o = t.one("SELECT * FROM billing_orders WHERE order_ref = %s FOR UPDATE", (ref,))
        sub = t.one("SELECT * FROM billing_subscriptions WHERE id = %s FOR UPDATE", (o["subscription_id"],))
        n = billing.now()
        g = str(resp.get("status") or "").upper()
        got_amount = resp.get("amount")
        same = (resp.get("order_id") == ref and str(resp.get("currency") or CURRENCY).upper() == o["currency"]
                and got_amount is not None and billing.money(got_amount) == billing.money(o["amount"]))
        if g != (o["gateway_status"] or "") or source in ("return", "webhook"):
            _event(t, "status", ref, g, hdfc.redact(resp))
        if not same:
            # not this order, or not its amount / currency: nothing is activated, a person looks
            if o["status"] not in ("paid", "partially_refunded", "refunded"):
                t.run("UPDATE billing_orders SET status = 'unknown', failure_reason = 'mismatch', gateway_status = %s, "
                      "last_checked_at = %s, updated_at = %s WHERE id = %s", (g, n, n, o["id"]))
            _audit(t, "system", "gateway_mismatch", ref, o["customer_id"],
                   {"order_id_ok": resp.get("order_id") == ref, "amount": str(got_amount), "currency": resp.get("currency")})
            return t.one("SELECT * FROM billing_orders WHERE id = %s", (o["id"],))
        refunded = billing.money(resp.get("amount_refunded") or 0)
        new = _next_status(o, g, refunded, returned, n)
        if o["status"] in ("paid", "partially_refunded", "refunded") and g not in hdfc.PAID | hdfc.REFUNDED:
            g = o["gateway_status"]              # an out-of-order answer: recorded above, not taken
        txn = resp.get("txn_detail") if isinstance(resp.get("txn_detail"), dict) else {}
        reason = None
        if new in ("failed", "unknown"):
            reason = (str(resp.get("bank_error_message") or txn.get("error_message") or g) or "")[:200]
        t.run("""UPDATE billing_orders SET status = %s, gateway_status = %s, gateway_status_id = %s, txn_id = COALESCE(%s, txn_id),
                   payment_method_type = COALESCE(%s, payment_method_type), payment_method = COALESCE(%s, payment_method),
                   amount_refunded = %s, failure_reason = COALESCE(%s, failure_reason), last_checked_at = %s, updated_at = %s
                 WHERE id = %s""",
              (new, g, resp.get("status_id"), resp.get("txn_id") or None, resp.get("payment_method_type") or None,
               resp.get("payment_method") or None, max(refunded, billing.money(o["amount_refunded"])), reason, n, n, o["id"]))
        if new != o["status"]:
            _audit(t, "system", "order_" + new, ref, o["customer_id"], {"from": o["status"], "gateway_status": g, "via": source})
        if g in hdfc.PAID and not o["activated_at"] and new == "paid":
            _activate(t, o, sub)
            activated = True
        elif new in ("failed", "cancelled", "expired") and sub["status"] == "payment_pending":
            t.run("UPDATE billing_subscriptions SET status = 'payment_failed', updated_at = %s WHERE id = %s", (n, sub["id"]))
        if new == "refunded" and o["activated_at"] and sub["status"] in ("active", "grace") \
                and billing.parse(sub["current_period_end"]) == billing.parse(o["period_end"]):
            # the whole payment for the running period refunded: that period's access ends
            t.run("UPDATE billing_subscriptions SET status = 'cancelled', cancelled_at = %s, updated_at = %s WHERE id = %s",
                  (n, n, sub["id"]))
            _audit(t, "system", "subscription_cancelled", ref, o["customer_id"], {"reason": "refunded"})
        for r in resp.get("refunds") or []:
            if isinstance(r, dict) and r.get("unique_request_id"):
                st = _REFUND_STATUS.get(str(r.get("status") or "").upper(), "pending")
                t.run("""UPDATE billing_refunds SET status = %s, gateway_status = %s, gateway_ref = COALESCE(%s, gateway_ref),
                           updated_at = %s WHERE unique_request_id = %s AND order_id = %s AND status <> %s""",
                      (st, str(r.get("status") or ""), r.get("ref") or None, n, r["unique_request_id"], o["id"], st))
        row = t.one("SELECT * FROM billing_orders WHERE id = %s", (o["id"],))
    if activated:
        _send_receipt(ref)
    return row


def handle_return(params):
    """The customer's browser back from HDFC's payment page (GET or POST to the
    return URL). The parameters only say which order; its status comes from the
    Order Status API. Returns the order reference, or None."""
    ref = str(params.get("order_id") or "")
    if not _REF.match(ref):
        return None
    valid = hdfc.verify_return_signature(params)
    with billing.tx() as t:
        known = t.one("SELECT order_ref FROM billing_orders WHERE order_ref = %s", (ref,))
        if not known:
            return None
        _event(t, "return", ref, str(params.get("status") or "")[:40] or None,
               {"status": params.get("status"), "status_id": params.get("status_id")}, signature_valid=valid)
        if valid is False:
            _audit(t, "system", "return_signature_invalid", ref)
    if valid is False:
        print(f"[payments] return for {ref}: signature did not verify; checking with the Order Status API", flush=True)
    reconcile(ref, "return", returned=True)
    return ref


def webhook(authorization, raw):
    """POST /api/payment/webhook. (status, body). 200 once handled (HDFC sends a
    webhook again until it gets a 200); a repeat of a handled event is a 200 too."""
    if not (os.environ.get("HDFC_WEBHOOK_USERNAME") and os.environ.get("HDFC_WEBHOOK_PASSWORD")):
        print("[payments] webhook refused: HDFC_WEBHOOK_USERNAME / HDFC_WEBHOOK_PASSWORD not set", flush=True)
        return 503, {"detail": "not_configured"}
    if not hdfc.webhook_authorized(authorization):
        return 401, {"detail": "unauthorized"}
    try:
        evt = json.loads(raw.decode("utf-8") or "{}")
        order = ((evt.get("content") or {}).get("order") or {})
        eid, name, ref = str(evt.get("id") or "")[:80], str(evt.get("event_name") or "")[:60], str(order.get("order_id") or "")
    except (ValueError, UnicodeDecodeError, AttributeError):
        return 400, {"detail": "bad_request"}
    if not eid:
        return 400, {"detail": "bad_request"}
    with billing.tx() as t:
        if t.one("SELECT id FROM billing_gateway_events WHERE source = 'webhook' AND event_id = %s", (eid,)):
            return 200, {"ok": True, "duplicate": True}
        known = _REF.match(ref) and t.one("SELECT id FROM billing_orders WHERE order_ref = %s", (ref,))
    if known:
        row = reconcile(ref, "webhook")
        if row and row.get("check_failed"):
            return 503, {"detail": "status_check_failed"}      # HDFC will send it again
    with billing.tx() as t:
        try:
            _event(t, "webhook", ref if known else None, str(order.get("status") or "")[:40] or None,
                   {"event_name": name, "order": hdfc.redact(order)}, event_id=eid, event_name=name)
        except Exception:  # noqa: BLE001 — the same event, recorded meanwhile by a parallel delivery
            pass
    return 200, {"ok": True}


# ---- the customer ------------------------------------------------------------------------

def _order_for(token):
    p = trial.unsign(token, "po")
    if time.time() >= p["x"]:
        raise PaymentError(401, "invalid_pass")
    with billing.tx() as t:
        o = t.one("""SELECT o.*, c.email, c.pw_hash, c.id AS cust_id FROM billing_orders o
                     JOIN billing_customers c ON c.id = o.customer_id WHERE o.order_ref = %s""", (p["r"],))
    if not o or o["email"] != p["e"]:
        raise PaymentError(404, "not_found")                 # the same answer for "not yours" and "not there"
    return o


def _view(o):
    with billing.tx() as t:
        inv = t.one("SELECT invoice_no, issued_at FROM billing_invoices WHERE order_id = %s", (o["id"],))
        sub = t.one("SELECT * FROM billing_subscriptions WHERE id = %s", (o["subscription_id"],))
        sub = _refresh(t, sub)
        cust = t.one("SELECT email, pw_hash FROM billing_customers WHERE id = %s", (o["customer_id"],))
    paid = o["status"] in ("paid", "partially_refunded") and o["activated_at"]
    return {"ok": True, "order_ref": o["order_ref"], "status": o["status"], "plan": o["plan_code"],
            "plan_name": PLANS[o["plan_code"]]["name"], "amount": str(billing.money(o["amount"])),
            "list_base": str(billing.money(o["list_base"])), "tax_percent": str(o["tax_percent"]),
            "list_tax": str(billing.money(o["list_tax"])), "list_total": str(billing.money(o["list_total"])),
            "currency": o["currency"], "environment": o["environment"], "paid_at": o["paid_at"],
            "period_start": o["period_start"], "period_end": o["period_end"],
            "invoice_no": inv["invoice_no"] if inv else None, "subscription_status": sub["status"],
            "payment_method": o["payment_method"] or None, "failure_reason": _public_reason(o),
            "can_retry": o["status"] in ("failed", "cancelled", "expired"), "email": cust["email"],
            "account": ("exists" if cust["pw_hash"] else "needed") if paid else None}


def _public_reason(o):
    if o["status"] not in ("failed", "unknown"):
        return None
    r = str(o["failure_reason"] or "")
    if r.startswith("gateway_") or r == "mismatch":
        return None                                           # internal; the page says it generally
    return r[:160] or None


def status(token, ip=""):
    if _limit("st-ip:" + (ip or "?"), 600):
        raise PaymentError(429, "too_many_attempts")
    o = _order_for(token)
    last = billing.parse(o["last_checked_at"])
    if o["status"] in ("pending", "unknown", "cancelled") and (not last or (billing.now() - last).total_seconds() >= 10):
        if o["status"] != "unknown" or o["failure_reason"] != "mismatch":
            reconcile(o["order_ref"], "status")
            o = _order_for(token)
    return _view(o)


def retry(token, ip=""):
    """A new order for the same customer and plan, after a failed, cancelled or
    expired one (an order id is never paid twice)."""
    ready()
    o = _order_for(token)
    if o["status"] not in ("failed", "cancelled", "expired"):
        raise PaymentError(409, "not_retryable")
    _checkout_allowed(ip, o["email"])
    with billing.tx() as t:
        cust = t.one("SELECT * FROM billing_customers WHERE id = %s FOR UPDATE", (o["customer_id"],))
        sub = _refresh(t, t.one("SELECT * FROM billing_subscriptions WHERE id = %s FOR UPDATE", (o["subscription_id"],)))
        kind = "renewal" if sub["status"] in ("active", "grace") else "new"
        ref = _new_order(t, cust, sub, o["plan_code"], kind, o["terms_version"])
        _audit(t, "customer", "order_retried", ref, cust["id"], {"previous": o["order_ref"]})
    return _open_session(ref, cust, o["plan_code"])


def account(token, password):
    """The customer's password, once their first payment is verified: it signs
    them in to WAREXX for their paid period."""
    o = _order_for(token)
    if not (o["status"] in ("paid", "partially_refunded") and o["activated_at"]):
        raise PaymentError(409, "not_paid")
    if not isinstance(password, str) or len(password) < trial.MIN_PASSWORD:
        raise PaymentError(422, "weak_password")
    salt, hash_ = trial.password_record(password)
    with billing.tx() as t:
        cust = t.one("SELECT * FROM billing_customers WHERE id = %s FOR UPDATE", (o["customer_id"],))
        if cust["pw_hash"]:
            raise PaymentError(409, "account_exists")
        n = billing.now()
        t.run("UPDATE billing_customers SET pw_salt = %s, pw_hash = %s, account_created_at = %s, updated_at = %s WHERE id = %s",
              (salt, hash_, n, n, cust["id"]))
        _audit(t, "customer", "account_created", o["order_ref"], cust["id"])
    rows = subscriber_logins(o["email"])
    return trial.signin(rows, o["email"], password)


def subscriber_logins(email):
    """A customer's account for the email + password sign-in (trial.signin): its
    window is the paid period (with any grace), so an expired, cancelled or
    unpaid subscription is refused there too."""
    email = str(email or "").strip().lower()
    if not email or not (billing.database_url() or os.path.exists(billing._sqlite_path())):
        return []
    with billing.tx() as t:
        c = t.one("SELECT * FROM billing_customers WHERE email = %s AND pw_hash IS NOT NULL", (email,))
        if not c:
            return []
        sub = t.one("SELECT * FROM billing_subscriptions WHERE customer_id = %s", (c["id"],))
        sub = _refresh(t, sub) if sub else None
    if not sub or not sub["current_period_start"]:
        return []
    end = billing.parse(sub["grace_until"]) or billing.parse(sub["current_period_end"])
    if sub["status"] not in ("active", "grace"):
        end = min(end, billing.parse(sub["updated_at"]))
    return [{"ref": f"cust:{c['id']}", "window_start": int(billing.parse(sub["current_period_start"]).timestamp()),
             "window_end": int(end.timestamp()), "pw_salt": c["pw_salt"], "pw_hash": c["pw_hash"], "kind": "sub"}]


def subscriber_access(email):
    """Backend enforcement for a subscriber's session: only an active (or grace)
    subscription opens WAREXX, whatever the session pass says."""
    try:
        with billing.tx() as t:
            c = t.one("SELECT id FROM billing_customers WHERE email = %s", (str(email or "").lower(),))
            sub = c and t.one("SELECT * FROM billing_subscriptions WHERE customer_id = %s", (c["id"],))
            sub = _refresh(t, sub) if sub else None
    except Exception as exc:  # noqa: BLE001 — unknown is not access
        print(f"[payments] access check failed: {type(exc).__name__}", flush=True)
        return False
    return bool(sub and sub["status"] in ("active", "grace"))


def subscriber_logged_in(ref):
    try:
        cid = int(str(ref).split(":", 1)[1])
        with billing.tx() as t:
            t.run("UPDATE billing_customers SET last_login_at = %s, logins = logins + 1 WHERE id = %s", (billing.now(), cid))
    except Exception as exc:  # noqa: BLE001
        print(f"[payments] sign-in not recorded: {type(exc).__name__}", flush=True)


def billing_history(session_token):
    """The signed-in customer's subscription and payments — theirs only: whose
    they are comes from the session, never from the request."""
    email = _session_email(session_token)
    if not email:
        raise PaymentError(401, "signed_out")
    with billing.tx() as t:
        c = t.one("SELECT * FROM billing_customers WHERE email = %s", (email,))
        if not c:
            raise PaymentError(404, "not_found")
        sub = _refresh(t, t.one("SELECT * FROM billing_subscriptions WHERE customer_id = %s", (c["id"],)))
        orders = t.all("""SELECT o.order_ref, o.created_at, o.plan_code, o.kind, o.amount, o.currency, o.status, o.environment,
                                 o.paid_at, o.period_start, o.period_end, o.amount_refunded, i.invoice_no
                          FROM billing_orders o LEFT JOIN billing_invoices i ON i.order_id = o.id
                          WHERE o.customer_id = %s ORDER BY o.created_at DESC LIMIT 50""", (c["id"],))
    return {"ok": True, "email": email, "company": c["company"], "workspace": c["tenant_slug"],
            "subscription": {"plan": sub["plan_code"], "plan_name": PLANS[sub["plan_code"]]["name"], "status": sub["status"],
                             "period_start": sub["current_period_start"], "period_end": sub["current_period_end"],
                             "grace_until": sub["grace_until"]},
            "orders": [{**o, "amount": str(billing.money(o["amount"])), "amount_refunded": str(billing.money(o["amount_refunded"])),
                        "plan_name": PLANS[o["plan_code"]]["name"]} for o in orders]}


# ---- the customer's profile (the WAREXX app → My profile → Subscription & billing) ----------
# Every action takes the customer's own session (a signed pass, "p": "sub"):
# whose account it is comes from there, never from the request.

def _me(t, session_token, lock=False):
    email = _session_email(session_token)
    if not email:
        raise PaymentError(401, "signed_out")
    c = t.one("SELECT * FROM billing_customers WHERE email = %s" + (" FOR UPDATE" if lock else ""), (email,))
    if not c:
        raise PaymentError(404, "not_found")
    sub = t.one("SELECT * FROM billing_subscriptions WHERE customer_id = %s" + (" FOR UPDATE" if lock else ""), (c["id"],))
    return c, (_refresh(t, sub) if sub else None)


_DETAILS = ("name", "phone", "company", "gstin", "address", "city", "state", "postcode", "country")


def account_info(session_token):
    """The profile section: the account, the subscription, the payments. A trial
    visitor's session (no subscription) answers who they are and until when."""
    try:
        s = trial.unsign(session_token, "s")
    except trial.TrialError:
        raise PaymentError(401, "signed_out")
    if s.get("p") != "sub":
        return {"ok": True, "kind": "trial", "email": s["e"], "trial_end": s["x"]}
    with billing.tx() as t:
        c, sub = _me(t, session_token)
        orders = t.all("""SELECT o.order_ref, o.created_at, o.plan_code, o.kind, o.amount, o.list_base, o.tax_percent, o.list_tax,
                                 o.list_total, o.currency, o.status, o.environment, o.paid_at, o.period_start, o.period_end,
                                 o.amount_refunded, o.payment_method, i.invoice_no
                          FROM billing_orders o LEFT JOIN billing_invoices i ON i.order_id = o.id
                          WHERE o.customer_id = %s AND o.status <> 'created' ORDER BY o.created_at DESC LIMIT 50""", (c["id"],))
    money_keys = ("amount", "list_base", "list_tax", "list_total", "amount_refunded")
    return {"ok": True, "kind": "subscriber", "email": c["email"], "workspace": c["tenant_slug"],
            "details": {k: c[k] or "" for k in _DETAILS},
            "account_created_at": c["account_created_at"], "last_login_at": c["last_login_at"],
            "subscription": None if not sub else {
                "plan": sub["plan_code"], "plan_name": PLANS[sub["plan_code"]]["name"], "status": sub["status"],
                "period_start": sub["current_period_start"], "period_end": sub["current_period_end"],
                "grace_until": sub["grace_until"], "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
                "cancel_requested_at": sub.get("cancel_requested_at"), "cancelled_at": sub["cancelled_at"]},
            "plans": {code: {"name": p["name"], "period": p["period"]} for code, p in PLANS.items()},
            "orders": [{**o, **{k: str(billing.money(o[k])) for k in money_keys}, "tax_percent": str(o["tax_percent"]),
                        "plan_name": PLANS[o["plan_code"]]["name"]} for o in orders]}


def update_details(session_token, data):
    """The billing details on future orders and receipts. The email (the sign-in)
    and past receipts do not change."""
    f = {k: re.sub(r"\s+", " ", str(data.get(k) or "")).strip()[:LIMITS[k]] for k in _DETAILS}
    f["gstin"] = f["gstin"].upper()
    bad = {}
    for k in ("name", "company", "address", "city", "state", "country"):
        if not f[k]:
            bad[k] = "required"
    if len(hdfc.clean_phone(f["phone"])) < 10:
        bad["phone"] = "invalid"
    if not f["postcode"]:
        bad["postcode"] = "required"
    elif f["country"] == "India" and not re.fullmatch(r"\d{6}", f["postcode"]):
        bad["postcode"] = "invalid"
    if f["gstin"] and not _GSTIN.match(f["gstin"]):
        bad["gstin"] = "invalid"
    if bad:
        raise PaymentError(422, "invalid_fields", fields=bad)
    with billing.tx() as t:
        c, _ = _me(t, session_token, lock=True)
        t.run("""UPDATE billing_customers SET name = %s, phone = %s, company = %s, gstin = %s, address = %s, city = %s,
                   state = %s, postcode = %s, country = %s, updated_at = %s WHERE id = %s""",
              (f["name"], f["phone"], f["company"], f["gstin"] or None, f["address"], f["city"], f["state"], f["postcode"],
               f["country"], billing.now(), c["id"]))
        _audit(t, "customer", "details_updated", None, c["id"], {"fields": sorted(k for k in _DETAILS if (c[k] or "") != f[k])})
    return {"ok": True, "details": f}


def change_password(session_token, current, new):
    if not isinstance(new, str) or len(new) < trial.MIN_PASSWORD:
        raise PaymentError(422, "weak_password")
    with billing.tx() as t:
        c, _ = _me(t, session_token, lock=True)
        if not c["pw_hash"] or not trial.hmac.compare_digest(trial._hash(str(current or ""), c["pw_salt"]), c["pw_hash"]):
            raise PaymentError(401, "wrong_password")
        salt, hash_ = trial.password_record(new)
        t.run("UPDATE billing_customers SET pw_salt = %s, pw_hash = %s, updated_at = %s WHERE id = %s",
              (salt, hash_, billing.now(), c["id"]))
        _audit(t, "customer", "password_changed", None, c["id"])
    return {"ok": True}


def cancel(session_token, reason=""):
    """Cancel: nothing is charged again (payments are one-time anyway), and the
    paid period runs to its end — then the subscription ends. No refund is made
    here; refunds follow the Refund & Cancellation Policy, through the team."""
    with billing.tx() as t:
        c, sub = _me(t, session_token, lock=True)
        if not sub or sub["status"] not in ("active", "grace"):
            raise PaymentError(409, "not_active")
        if sub.get("cancel_at_period_end"):
            return {"ok": True, "already": True, "ends_at": sub["current_period_end"]}
        n = billing.now()
        t.run("""UPDATE billing_subscriptions SET cancel_at_period_end = %s, cancel_requested_at = %s, cancel_reason = %s,
                   updated_at = %s WHERE id = %s""", (True, n, str(reason or "")[:300] or None, n, sub["id"]))
        _audit(t, "customer", "cancel_requested", None, c["id"], {"ends_at": sub["current_period_end"]})
    _send_cancel_note(c, sub, str(reason or "")[:300])
    return {"ok": True, "ends_at": sub["current_period_end"]}


def resume(session_token):
    """Undo a cancellation before the period ends."""
    with billing.tx() as t:
        c, sub = _me(t, session_token, lock=True)
        if not sub or sub["status"] not in ("active", "grace") or not sub.get("cancel_at_period_end"):
            raise PaymentError(409, "not_cancelled")
        t.run("""UPDATE billing_subscriptions SET cancel_at_period_end = %s, cancel_requested_at = NULL, cancel_reason = NULL,
                   updated_at = %s WHERE id = %s""", (False, billing.now(), sub["id"]))
        _audit(t, "customer", "cancel_withdrawn", None, c["id"])
    return {"ok": True}


def receipt(session_token, order_ref):
    """One of the customer's own receipts (a paid order), for printing."""
    with billing.tx() as t:
        c, _ = _me(t, session_token)
        r = t.one("""SELECT i.invoice_no, i.issued_at, i.base, i.tax_percent, i.tax, i.total, i.amount_charged, i.currency,
                            i.environment, i.gstin, i.bill_to, o.order_ref, o.plan_code, o.period_start, o.period_end,
                            o.payment_method, o.txn_id, o.amount_refunded
                     FROM billing_invoices i JOIN billing_orders o ON o.id = i.order_id
                     WHERE o.order_ref = %s AND i.customer_id = %s""", (str(order_ref or ""), c["id"]))
    if not r:
        raise PaymentError(404, "not_found")
    for k in ("base", "tax", "total", "amount_charged", "amount_refunded"):
        r[k] = str(billing.money(r[k]))
    r["tax_percent"] = str(r["tax_percent"])
    return {"ok": True, "receipt": {**r, "plan_name": PLANS[r["plan_code"]]["name"], "customer": c["name"],
                                    "email": c["email"], "company": c["company"]}}


def _send_cancel_note(c, sub, reason):
    try:
        end = str(sub["current_period_end"] or "")[:10]
        text = (f"Hi {(c['name'].split() or ['there'])[0]},\n\nYour WAREXX subscription is cancelled. You keep access until "
                f"{end}; nothing more will be charged.\n\nChanged your mind? Open WAREXX → My profile → Subscription & billing "
                f"→ Keep my subscription, any time before {end}.\n\nTeam WAREXX\n")
        lead_mail._deliver(lead_mail._message(c["email"], "Your WAREXX subscription is cancelled", text,
                                              lead_mail._wrap("<p>" + html.escape(text).replace("\n\n", "</p><p>") + "</p>")))
        lead_mail._deliver(lead_mail._message(lead_mail.ACCOUNTS_TO, f"WAREXX subscription cancelled: {c['company']}",
                                              f"{c['company']} ({c['email']}) cancelled; access ends {end}.\nReason: {reason or '-'}\n",
                                              lead_mail._wrap(f"<p>{html.escape(c['company'])} ({html.escape(c['email'])}) cancelled; "
                                                              f"access ends {html.escape(end)}.</p><p>Reason: {html.escape(reason or '-')}</p>")))
    except Exception as exc:  # noqa: BLE001
        print(f"[payments] cancellation email not sent: {type(exc).__name__}", flush=True)


# ---- the team (admin.py) --------------------------------------------------------------------

def admin_orders(q="", status_="", limit=50, offset=0):
    where, params = [], []
    if status_:
        where.append("o.status = %s"); params.append(status_)
    if q:
        like = "%" + q.lower() + "%"
        where.append("(LOWER(c.email) LIKE %s OR LOWER(c.company) LIKE %s OR LOWER(o.order_ref) LIKE %s OR LOWER(c.name) LIKE %s)")
        params += [like, like, like, like]
    w = (" WHERE " + " AND ".join(where)) if where else ""
    with billing.tx() as t:
        total = t.one(f"SELECT COUNT(*) AS n FROM billing_orders o JOIN billing_customers c ON c.id = o.customer_id{w}", params)["n"]
        rows = t.all(f"""SELECT o.order_ref, o.created_at, o.plan_code, o.kind, o.environment, o.amount, o.list_total, o.currency,
                                o.status, o.gateway_status, o.payment_method, o.amount_refunded, o.paid_at, o.period_end,
                                o.failure_reason, c.name, c.email, c.phone, c.company, c.gstin, c.tenant_slug,
                                s.status AS subscription_status, s.cancel_at_period_end, i.invoice_no
                         FROM billing_orders o JOIN billing_customers c ON c.id = o.customer_id
                         JOIN billing_subscriptions s ON s.id = o.subscription_id
                         LEFT JOIN billing_invoices i ON i.order_id = o.id{w}
                         ORDER BY o.created_at DESC LIMIT {int(limit)} OFFSET {int(offset)}""", params)
        for r in rows:
            r["refunds"] = t.all("""SELECT r.unique_request_id, r.amount, r.status, r.created_at FROM billing_refunds r
                                    JOIN billing_orders o ON o.id = r.order_id WHERE o.order_ref = %s ORDER BY r.created_at""",
                                 (r["order_ref"],))
            for x in r["refunds"]:
                x["amount"] = str(billing.money(x["amount"]))
            for k in ("amount", "list_total", "amount_refunded"):
                r[k] = str(billing.money(r[k]))
    return {"total": int(total), "rows": rows}


def refund(ref, amount, reason, actor):
    """A refund through HDFC's Refund Order API — the team only (admin.py). It
    must be for a paid order, and no more than is left unrefunded counting the
    refunds already asked for; its unique_request_id is kept before HDFC is
    asked, so a repeat cannot refund twice."""
    hdfc.config(require_return=False)
    try:
        amt = billing.money(amount)
    except (InvalidOperation, TypeError):
        raise PaymentError(422, "invalid_amount")
    if amt <= 0:
        raise PaymentError(422, "invalid_amount")
    uid = "RF" + secrets.token_hex(8).upper()
    with billing.tx() as t:
        o = t.one("""SELECT o.*, c.gateway_customer_id FROM billing_orders o JOIN billing_customers c ON c.id = o.customer_id
                     WHERE o.order_ref = %s FOR UPDATE""", (str(ref),))
        if not o:
            raise PaymentError(404, "not_found")
        if o["status"] not in ("paid", "partially_refunded"):
            raise PaymentError(409, "not_refundable")       # HDFC refunds CHARGED orders only
        asked = t.all("""SELECT amount FROM billing_refunds WHERE order_id = %s
                         AND status IN ('requested', 'pending', 'success', 'manual_review')""", (o["id"],))
        left = billing.money(o["amount"]) - sum((billing.money(a["amount"]) for a in asked), Decimal("0"))
        if amt > left:
            raise PaymentError(422, "exceeds_refundable", refundable=str(left))
        n = billing.now()
        t.run("""INSERT INTO billing_refunds (order_id, unique_request_id, amount, status, reason, requested_by, created_at, updated_at)
                 VALUES (%s, %s, %s, 'requested', %s, %s, %s, %s)""", (o["id"], uid, amt, str(reason or "")[:300], actor, n, n))
        _audit(t, actor, "refund_requested", ref, o["customer_id"], {"amount": str(amt), "unique_request_id": uid})
    try:
        resp = hdfc.refund(ref, o["gateway_customer_id"], uid, str(amt))
    except hdfc.GatewayError as e:
        with billing.tx() as t:
            if e.kind == "rejected":
                t.run("UPDATE billing_refunds SET status = 'error', error = %s, updated_at = %s WHERE unique_request_id = %s",
                      (f"{e.status} {e.code} {e.message}"[:300], billing.now(), uid))
            _event(t, "refund", ref, None, {"unique_request_id": uid, "error": e.kind, "http_status": e.status, "code": e.code})
            _audit(t, actor, "refund_" + ("rejected" if e.kind == "rejected" else "unconfirmed"), ref, o["customer_id"],
                   {"unique_request_id": uid, "code": e.code})
        if e.kind == "rejected":
            raise PaymentError(502, "refund_rejected", code=e.code)
        raise PaymentError(503, "refund_unconfirmed", unique_request_id=uid)   # "Check with HDFC" settles it
    with billing.tx() as t:
        _event(t, "refund", ref, str(resp.get("status") or ""), hdfc.redact(resp))
    row = _apply(ref, resp, "refund")
    with billing.tx() as t:
        r = t.one("SELECT unique_request_id, amount, status, gateway_ref FROM billing_refunds WHERE unique_request_id = %s", (uid,))
        if r["status"] == "requested":     # HDFC answered, but without this refund in its list
            t.run("UPDATE billing_refunds SET status = 'pending', updated_at = %s WHERE unique_request_id = %s",
                  (billing.now(), uid))
            r["status"] = "pending"
    return {"ok": True, "refund": {**r, "amount": str(billing.money(r["amount"]))}, "order_status": row["status"]}


def summary():
    """The admin page's totals; None where there is no billing database yet (a
    read never creates one)."""
    if not billing.database_url() and not os.path.exists(billing._sqlite_path()):
        return None
    with billing.tx() as t:
        one = lambda q: int(t.one(q)["n"])  # noqa: E731
        return {"orders": one("SELECT COUNT(*) AS n FROM billing_orders"),
                "paid": one("SELECT COUNT(*) AS n FROM billing_orders WHERE status IN ('paid', 'partially_refunded')"),
                "active_subscriptions": one("SELECT COUNT(*) AS n FROM billing_subscriptions WHERE status IN ('active', 'grace')")}


# ---- emails -------------------------------------------------------------------------------

def _send_receipt(ref):
    """The customer's payment receipt, and the team's copy. Never raises."""
    try:
        with billing.tx() as t:
            o = t.one("""SELECT o.*, c.email, c.name, c.company, c.phone, c.gstin, c.tenant_slug, i.invoice_no
                         FROM billing_orders o JOIN billing_customers c ON c.id = o.customer_id
                         LEFT JOIN billing_invoices i ON i.order_id = o.id WHERE o.order_ref = %s""", (ref,))
        test = o["environment"] == "sandbox"
        rows = [("Receipt", o["invoice_no"]), ("Order", o["order_ref"]), ("Plan", PLANS[o["plan_code"]]["name"]),
                ("Price", f"₹{billing.money(o['list_base']):,}"), (f"GST ({o['tax_percent']}%)", f"₹{billing.money(o['list_tax']):,}"),
                ("Total", f"₹{billing.money(o['list_total']):,}"),
                ("Charged", f"₹{billing.money(o['amount']):,}" + (" (HDFC sandbox test amount)" if test else "")),
                ("Paid with", o["payment_method"] or "—"),
                ("Subscription", f"{str(o['period_start'])[:10]} to {str(o['period_end'])[:10]}")]
        table = "".join(f'<tr><td style="padding:3px 16px 3px 0;color:#6E675E">{html.escape(k)}</td>'
                        f"<td>{html.escape(str(v or '—'))}</td></tr>" for k, v in rows)
        first = (o["name"].split() or ["there"])[0]
        text = (f"Hi {first},\n\nThank you — your payment for WAREXX is confirmed" + (" (sandbox test)" if test else "") + ".\n\n"
                + "\n".join(f"{k}: {v or '-'}" for k, v in rows)
                + "\n\nYou can sign in to WAREXX with this email address and the password you choose after paying.\n\n"
                  f"Questions? {lead_mail.SUPPORT_EMAIL}\n\nTeam WAREXX\n")
        m = lead_mail._message(o["email"], "Your WAREXX payment receipt " + (o["invoice_no"] or o["order_ref"]), text,
                               lead_mail._wrap(f"<p>Hi {html.escape(first)},</p><p>Thank you — your payment for WAREXX is "
                                               f"confirmed{' (sandbox test)' if test else ''}.</p><table>{table}</table>"))
        lead_mail._deliver(m)
        team = lead_mail._message(lead_mail.ACCOUNTS_TO, f"WAREXX subscription paid: {o['company']} ({PLANS[o['plan_code']]['name']})"
                                  + (" [sandbox]" if test else ""),
                                  "\n".join(f"{k}: {v or '-'}" for k, v in rows + [("Customer", o["name"]), ("Email", o["email"]),
                                                                                    ("Phone", o["phone"]), ("Company", o["company"]),
                                                                                    ("GSTIN", o["gstin"]), ("Workspace", o["tenant_slug"])]) + "\n",
                                  lead_mail._wrap(f"<p>A subscription was paid{' (sandbox)' if test else ''}. "
                                                  f"Set up the workspace <b>{html.escape(o['tenant_slug'])}</b>.</p><table>{table}</table>"))
        lead_mail._deliver(team)
    except Exception as exc:  # noqa: BLE001
        print(f"[payments] receipt for {ref} not sent: {type(exc).__name__}", flush=True)
