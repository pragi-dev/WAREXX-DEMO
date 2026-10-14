"""HDFC Bank SmartGateway — the server-to-server client (Basic Auth API reference).

Official documentation (SmartGateway API Reference, Basic Authentication):
  https://smartgateway.hdfc.bank.in/docs/smartgateway-api-ref-basicauth/docs/overview/integration-architecture
  Session API             POST {base}/session
  Order Status API        GET  {base}/orders/{order_id}
  Refund Order API        POST {base}/orders/{order_id}/refunds
  Return URL signature    HMAC-SHA256 with the Response Key ("Use signed response" on the dashboard)
  Webhooks                HTTPS POST, Basic Authorization = the username:password set on the dashboard

Every call is made from the server: the API key, the response key and the webhook
password are read from the environment here and nowhere else, and never logged,
returned to a browser or written to the database.

Settings (server-side environment variables):
  HDFC_ENV                     sandbox | production (no default: nothing is charged by accident)
  HDFC_BASE_URL                optional; when set it must be the documented URL for HDFC_ENV:
                                 sandbox     https://smartgateway.hdfcuat.bank.in
                                 production  https://smartgateway.hdfc.bank.in
  HDFC_MERCHANT_ID             the Merchant ID from the bank
  HDFC_API_KEY                 the API key from the SmartGateway dashboard
  HDFC_RESPONSE_KEY            the Response Key (return-URL signatures)
  HDFC_PAYMENT_PAGE_CLIENT_ID  sandbox: "hdfcmaster"; production: the Merchant ID (as documented)
  HDFC_RESELLER_ID             default "hdfc_reseller" (the documented value of x-resellerid)
  HDFC_RETURN_URL              where the payment page sends the customer back: this site's
                               /api/payment/return. Production: https, no query, no IP address.
  HDFC_WEBHOOK_USERNAME        the webhook's Basic Authorization, as set on the dashboard
  HDFC_WEBHOOK_PASSWORD
  HDFC_TIMEOUT_SECONDS         default 20
"""
import base64
import hmac
import hashlib
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import quote_plus, unquote_plus, urlencode, urlparse

BASE_URLS = {"sandbox": "https://smartgateway.hdfcuat.bank.in", "production": "https://smartgateway.hdfc.bank.in"}
RESELLER_ID = "hdfc_reseller"

#: Order statuses (Resources → Transaction Status), and what they mean for a WAREXX order.
PAID = {"CHARGED"}
FAILED = {"JUSPAY_DECLINED", "AUTHENTICATION_FAILED", "AUTHORIZATION_FAILED"}
PENDING = {"PENDING_VBV", "AUTHORIZING", "STARTED"}
NOT_STARTED = {"NEW"}
REFUNDED = {"AUTO_REFUNDED"}


class ConfigError(Exception):
    """Payments are not set up: `missing` names the settings (never their values)."""
    def __init__(self, missing):
        super().__init__("payments not configured: " + ", ".join(missing))
        self.missing = missing


class GatewayError(Exception):
    """HDFC answered with an error, or could not be reached. `kind`: rejected |
    unavailable | invalid_response. `status`: the HTTP status, when there was one."""
    def __init__(self, kind, status=None, code="", message=""):
        super().__init__(f"{kind} {status or ''} {code}".strip())
        self.kind, self.status, self.code, self.message = kind, status, code, message


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def config(require_return=True):
    """The checked settings, or ConfigError naming every missing or wrong one."""
    env = _env("HDFC_ENV").lower()
    c = {"env": env, "merchant_id": _env("HDFC_MERCHANT_ID"), "api_key": _env("HDFC_API_KEY"),
         "response_key": _env("HDFC_RESPONSE_KEY"), "client_id": _env("HDFC_PAYMENT_PAGE_CLIENT_ID"),
         "reseller_id": _env("HDFC_RESELLER_ID", RESELLER_ID), "return_url": _env("HDFC_RETURN_URL"),
         "webhook_user": _env("HDFC_WEBHOOK_USERNAME"), "webhook_password": _env("HDFC_WEBHOOK_PASSWORD")}
    bad = []
    if env not in BASE_URLS:
        bad.append("HDFC_ENV")
    for key, name in (("merchant_id", "HDFC_MERCHANT_ID"), ("api_key", "HDFC_API_KEY"),
                      ("response_key", "HDFC_RESPONSE_KEY"), ("client_id", "HDFC_PAYMENT_PAGE_CLIENT_ID")):
        if not c[key]:
            bad.append(name)
    base = _env("HDFC_BASE_URL").rstrip("/")
    if env in BASE_URLS:
        if base and base != BASE_URLS[env]:
            bad.append("HDFC_BASE_URL (not the documented URL for HDFC_ENV=" + env + ")")
        base = BASE_URLS[env]
        if env == "production" and c["client_id"] and c["client_id"] != c["merchant_id"]:
            bad.append("HDFC_PAYMENT_PAGE_CLIENT_ID (production: the Merchant ID)")
        if env == "production" and not (c["webhook_user"] and c["webhook_password"]):
            bad.append("HDFC_WEBHOOK_USERNAME / HDFC_WEBHOOK_PASSWORD")
    c["base_url"] = base
    if require_return:
        problem = return_url_problem(c["return_url"], env)
        if problem:
            bad.append("HDFC_RETURN_URL (" + problem + ")")
    if bad:
        raise ConfigError(bad)
    try:
        c["timeout"] = max(5, min(60, int(_env("HDFC_TIMEOUT_SECONDS", "20"))))
    except ValueError:
        c["timeout"] = 20
    return c


def return_url_problem(url, env):
    """The Session API's rules for return_url: fully qualified, no query
    parameters, no IP address, HTTPS (http://localhost is allowed in the sandbox
    only, for a developer's own machine)."""
    if not url:
        return "not set"
    u = urlparse(url)
    if u.query or u.fragment:
        return "must not contain query parameters"
    host = u.hostname or ""
    try:
        ipaddress.ip_address(host)
        return "must not be an IP address"
    except ValueError:
        pass
    if u.scheme == "https" and host:
        return ""
    if env == "sandbox" and u.scheme == "http" and host == "localhost":
        return ""
    return "must be an https:// address"


def configured():
    try:
        config()
        return True
    except ConfigError:
        return False


def missing():
    try:
        config()
        return []
    except ConfigError as e:
        return e.missing


# ---- the API --------------------------------------------------------------------

def _headers(c, customer_id, content_type="application/json"):
    # Authorization: "Basic " + Base64 of the API key, exactly as the API reference shows
    # (API key 1234 → "Basic MTIzNA==")
    return {"Authorization": "Basic " + base64.b64encode(c["api_key"].encode()).decode(),
            "x-merchantid": c["merchant_id"], "x-customerid": customer_id,
            "x-resellerid": c["reseller_id"], "Content-Type": content_type, "Accept": "application/json",
            # the gateway's edge refuses Python's default User-Agent (Cloudflare error 1010)
            "User-Agent": "WAREXX-Billing/1.0"}


#: Tests point this at a local fake of the gateway; nothing else changes it.
_opener = urllib.request.urlopen


def _call(method, url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _opener(req, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        raise GatewayError("unavailable", message=type(e).__name__)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise GatewayError("invalid_response", status)
    if status >= 500:
        raise GatewayError("unavailable", status, str(data.get("error_code") or ""))
    if status >= 400:
        raise GatewayError("rejected", status, str(data.get("error_code") or data.get("status") or ""),
                           str(data.get("error_message") or "")[:200])
    if not isinstance(data, dict):
        raise GatewayError("invalid_response", status)
    return data


def create_session(order_id, amount, customer_id, email, phone, first_name, last_name, description,
                   udf=None, currency="INR"):
    """Session API: the order at HDFC, and the payment page link for it.
    `amount` is a string with up to two decimals ("1.00", "35398.82")."""
    c = config()
    body = {"order_id": order_id, "amount": amount, "customer_id": customer_id,
            "customer_email": email, "customer_phone": phone, "payment_page_client_id": c["client_id"],
            "action": "paymentPage", "currency": currency, "return_url": c["return_url"],
            "description": description, "first_name": first_name, "last_name": last_name}
    body.update(udf or {})
    data = _call("POST", c["base_url"] + "/session", _headers(c, customer_id),
                 json.dumps(body).encode(), c["timeout"])
    link = ((data.get("payment_links") or {}).get("web") or "")
    if data.get("order_id") != order_id or not link_is_gateway(link, c):
        raise GatewayError("invalid_response", 200)
    return {"id": data.get("id") or "", "status": data.get("status") or "", "payment_link": link,
            "expiry": (data.get("payment_links") or {}).get("expiry") or ""}


def link_is_gateway(link, c=None):
    """The payment link must be on the SmartGateway host of the configured environment."""
    c = c or config(require_return=False)
    u, b = urlparse(link or ""), urlparse(c["base_url"])
    return u.scheme == "https" and u.hostname == b.hostname


def order_status(order_id, customer_id, retries=2):
    """Order Status API. A read, so a timeout or a 5xx is tried again (twice)."""
    c = config(require_return=False)
    for attempt in range(retries + 1):
        try:
            return _call("GET", c["base_url"] + "/orders/" + quote_plus(order_id),
                         _headers(c, customer_id), None, c["timeout"])
        except GatewayError as e:
            if e.kind != "unavailable" or attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))


def refund(order_id, customer_id, unique_request_id, amount):
    """Refund Order API (form-encoded, as the API reference's request). Not
    retried here: the caller keeps unique_request_id, so asking again later is
    safe — HDFC refuses a reused one."""
    c = config(require_return=False)
    body = urlencode({"unique_request_id": unique_request_id, "amount": amount}).encode()
    return _call("POST", c["base_url"] + "/orders/" + quote_plus(order_id) + "/refunds",
                 _headers(c, customer_id, "application/x-www-form-urlencoded"), body, c["timeout"])


# ---- what comes back --------------------------------------------------------------

def verify_return_signature(params, response_key=None):
    """Resources → HMAC Signature verification for return URL. True / False, or
    None when the return carries no signature ("Use signed response" is off).
    Either way the order is then checked with the Order Status API: the
    signature is a first check, never the reason an order counts as paid."""
    sig = params.get("signature")
    if not sig:
        return None
    key = response_key if response_key is not None else _env("HDFC_RESPONSE_KEY")
    if not key:
        return False
    pairs = sorted((quote_plus(str(k)), quote_plus(str(v))) for k, v in params.items()
                   if k not in ("signature", "signature_algorithm"))
    message = quote_plus("&".join(f"{k}={v}" for k, v in pairs))
    digest = base64.b64encode(hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()).decode()
    algo = str(params.get("signature_algorithm") or "HMAC-SHA256").upper()
    if algo != "HMAC-SHA256":
        return False
    return any(hmac.compare_digest(digest, s) for s in {sig, unquote_plus(sig)})


def webhook_authorized(authorization):
    """Webhooks: the Basic Authorization header must decode to the
    username:password configured on the dashboard (HDFC_WEBHOOK_*)."""
    user, password = _env("HDFC_WEBHOOK_USERNAME"), _env("HDFC_WEBHOOK_PASSWORD")
    if not (user and password) or not str(authorization or "").startswith("Basic "):
        return False
    try:
        got = base64.b64decode(authorization[6:].strip(), validate=True).decode("utf-8")
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(got.encode(), f"{user}:{password}".encode())


#: Kept from a gateway response (Order Status / webhook / refund) for the
#: records: identifiers and statuses only — no card block, no payer VPA, no
#: payment page payload or client tokens, no customer contact details.
_KEEP = ("id", "order_id", "status", "status_id", "amount", "currency", "date_created", "txn_id", "txn_uuid",
         "payment_method_type", "payment_method", "auth_type", "refunded", "amount_refunded", "effective_amount",
         "bank_error_code", "bank_error_message", "resp_code", "resp_message", "gateway_id")
_KEEP_TXN = ("txn_id", "status", "error_code", "error_message", "txn_amount", "net_amount", "currency", "gateway", "created")
_KEEP_REFUND = ("id", "unique_request_id", "ref", "amount", "status", "error_code", "error_message",
                "sent_to_gateway", "initiated_by", "created")


def redact(order):
    if not isinstance(order, dict):
        return {}
    out = {k: order.get(k) for k in _KEEP if k in order}
    t = order.get("txn_detail")
    if isinstance(t, dict):
        out["txn_detail"] = {k: t.get(k) for k in _KEEP_TXN if k in t}
    if isinstance(order.get("refunds"), list):
        out["refunds"] = [{k: r.get(k) for k in _KEEP_REFUND if k in r} for r in order["refunds"] if isinstance(r, dict)]
    return out


def clean_name(s, n=40):
    """first_name / last_name: alphanumerics and ().-_ only (Session API)."""
    return re.sub(r"[^A-Za-z0-9().\-_]", "", str(s or ""))[:n]


def clean_phone(s):
    """customer_phone: a 10-digit number without +91 (Session API recommendation)."""
    d = re.sub(r"\D", "", str(s or ""))
    return d[-10:] if len(d) >= 10 else d
