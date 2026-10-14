"""The billing database: customers, plans, subscriptions, orders, invoices,
refunds, the gateway's events and an audit trail. Its own database, apart from
the leads (store.py) and from the WAREXX software's operational data.

Which database:
    BILLING_DATABASE_URL — a postgres:// address (pg8000). Required on a hosted
    deployment. Not set on a developer's machine: a SQLite file,
    lead-api/data/billing.db (BILLING_SQLITE_PATH to move it).

Migrations are versioned (billing_schema_migrations) and only ever add: no
table, column or row is dropped or rewritten. Each runs in its own
transaction, once, under an advisory lock on PostgreSQL.

Money is NUMERIC(12,2) on PostgreSQL and decimal text on SQLite; the code uses
decimal.Decimal throughout, never floats.
"""
import contextlib
import datetime as dt
import json
import os
import sqlite3
import threading
from decimal import Decimal

import store

HERE = os.path.dirname(os.path.abspath(__file__))
_ready = set()
_lock = threading.Lock()

SUB_STATUSES = ("trial", "active", "payment_pending", "payment_failed", "grace", "restricted",
                "suspended", "cancelled", "expired")
ORDER_STATUSES = ("created", "pending", "paid", "failed", "cancelled", "expired", "refunded",
                  "partially_refunded", "unknown")
REFUND_STATUSES = ("requested", "pending", "success", "failure", "manual_review", "error")


#: BILLING_DATABASE_URL, or what Vercel's Neon integration sets when the billing
#: database is connected with the custom prefix BILLING or BILLING_DATABASE.
_URL_NAMES = ("BILLING_DATABASE_URL", "BILLING_DATABASE_POSTGRES_URL", "BILLING_DATABASE_DATABASE_URL",
              "BILLING_POSTGRES_URL")


def database_url():
    for name in _URL_NAMES:
        v = (os.environ.get(name) or "").strip()
        if v.startswith(("postgres://", "postgresql://")):
            return v
    return ""


def kind():
    return "postgres" if database_url() else "sqlite"


def _sqlite_path():
    return os.environ.get("BILLING_SQLITE_PATH") or os.path.join(HERE, "data", "billing.db")


def hosted():
    """A hosted deployment (Vercel), where a SQLite file would not last."""
    return bool(os.environ.get("VERCEL"))


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat() if t else None


def parse(t):
    if t is None or isinstance(t, dt.datetime):
        return t if t is None or t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))


def money(v):
    return Decimal(str(v if v is not None else "0")).quantize(Decimal("0.01"))


# ---- schema ------------------------------------------------------------------------

def _pg(sql):
    return sql


def _lite(sql):
    """The PostgreSQL DDL, for SQLite: the same tables and constraints, its types."""
    for a, b in (("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"), ("TIMESTAMPTZ", "TEXT"),
                 ("NUMERIC(12,2)", "TEXT"), ("NUMERIC(5,2)", "TEXT"), ("JSONB", "TEXT"), ("BIGINT", "INTEGER"),
                 ("BOOLEAN", "INTEGER"), ("CHAR(3)", "TEXT"), ("DEFAULT FALSE", "DEFAULT 0"),
                 ("DEFAULT TRUE", "DEFAULT 1")):
        sql = sql.replace(a, b)
    return sql


_V1 = [
    """CREATE TABLE billing_plans (
         code TEXT PRIMARY KEY,
         name TEXT NOT NULL,
         interval_unit TEXT NOT NULL CHECK (interval_unit IN ('month', 'year')),
         interval_count INTEGER NOT NULL CHECK (interval_count > 0),
         price NUMERIC(12,2) NOT NULL CHECK (price >= 0),
         currency CHAR(3) NOT NULL,
         active BOOLEAN NOT NULL DEFAULT TRUE,
         updated_at TIMESTAMPTZ NOT NULL)""",
    """CREATE TABLE billing_customers (
         id BIGSERIAL PRIMARY KEY,
         email TEXT NOT NULL UNIQUE CHECK (email = lower(email)),
         gateway_customer_id TEXT NOT NULL UNIQUE,
         tenant_slug TEXT NOT NULL UNIQUE,
         name TEXT NOT NULL, phone TEXT NOT NULL, company TEXT NOT NULL, gstin TEXT,
         address TEXT, city TEXT, state TEXT, postcode TEXT, country TEXT,
         pw_salt TEXT, pw_hash TEXT, account_created_at TIMESTAMPTZ,
         last_login_at TIMESTAMPTZ, logins INTEGER NOT NULL DEFAULT 0,
         created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
         CHECK ((pw_salt IS NULL) = (pw_hash IS NULL)))""",
    """CREATE TABLE billing_subscriptions (
         id BIGSERIAL PRIMARY KEY,
         customer_id BIGINT NOT NULL UNIQUE REFERENCES billing_customers (id),
         plan_code TEXT NOT NULL REFERENCES billing_plans (code),
         status TEXT NOT NULL CHECK (status IN ('trial', 'active', 'payment_pending', 'payment_failed', 'grace',
                                                'restricted', 'suspended', 'cancelled', 'expired')),
         current_period_start TIMESTAMPTZ, current_period_end TIMESTAMPTZ, grace_until TIMESTAMPTZ,
         cancelled_at TIMESTAMPTZ,
         created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
         CHECK (current_period_end IS NULL OR current_period_end > current_period_start))""",
    """CREATE TABLE billing_orders (
         id BIGSERIAL PRIMARY KEY,
         order_ref TEXT NOT NULL UNIQUE CHECK (length(order_ref) BETWEEN 8 AND 20),
         customer_id BIGINT NOT NULL REFERENCES billing_customers (id),
         subscription_id BIGINT NOT NULL REFERENCES billing_subscriptions (id),
         plan_code TEXT NOT NULL REFERENCES billing_plans (code),
         kind TEXT NOT NULL CHECK (kind IN ('new', 'renewal')),
         environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
         list_base NUMERIC(12,2) NOT NULL, tax_percent NUMERIC(5,2) NOT NULL,
         list_tax NUMERIC(12,2) NOT NULL, list_total NUMERIC(12,2) NOT NULL,
         amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
         currency CHAR(3) NOT NULL,
         status TEXT NOT NULL CHECK (status IN ('created', 'pending', 'paid', 'failed', 'cancelled', 'expired',
                                                'refunded', 'partially_refunded', 'unknown')),
         gateway_order_id TEXT, gateway_status TEXT, gateway_status_id INTEGER,
         payment_link TEXT, payment_link_expires_at TIMESTAMPTZ,
         txn_id TEXT, payment_method_type TEXT, payment_method TEXT, failure_reason TEXT,
         amount_refunded NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (amount_refunded >= 0),
         period_start TIMESTAMPTZ, period_end TIMESTAMPTZ, activated_at TIMESTAMPTZ,
         terms_version TEXT NOT NULL, terms_accepted_at TIMESTAMPTZ NOT NULL,
         last_checked_at TIMESTAMPTZ, paid_at TIMESTAMPTZ,
         created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""",
    """CREATE TABLE billing_invoices (
         id BIGSERIAL PRIMARY KEY,
         invoice_no TEXT NOT NULL UNIQUE,
         order_id BIGINT NOT NULL UNIQUE REFERENCES billing_orders (id),
         customer_id BIGINT NOT NULL REFERENCES billing_customers (id),
         issued_at TIMESTAMPTZ NOT NULL,
         base NUMERIC(12,2) NOT NULL, tax_percent NUMERIC(5,2) NOT NULL, tax NUMERIC(12,2) NOT NULL,
         total NUMERIC(12,2) NOT NULL, amount_charged NUMERIC(12,2) NOT NULL, currency CHAR(3) NOT NULL,
         environment TEXT NOT NULL, gstin TEXT, bill_to TEXT NOT NULL)""",
    """CREATE TABLE billing_refunds (
         id BIGSERIAL PRIMARY KEY,
         order_id BIGINT NOT NULL REFERENCES billing_orders (id),
         unique_request_id TEXT NOT NULL UNIQUE CHECK (length(unique_request_id) BETWEEN 8 AND 20),
         amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
         status TEXT NOT NULL CHECK (status IN ('requested', 'pending', 'success', 'failure', 'manual_review', 'error')),
         gateway_ref TEXT, gateway_status TEXT, error TEXT, reason TEXT, requested_by TEXT NOT NULL,
         created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""",
    """CREATE TABLE billing_gateway_events (
         id BIGSERIAL PRIMARY KEY,
         received_at TIMESTAMPTZ NOT NULL,
         source TEXT NOT NULL CHECK (source IN ('session', 'return', 'webhook', 'status', 'refund')),
         order_ref TEXT, event_id TEXT, event_name TEXT, gateway_status TEXT,
         signature_valid BOOLEAN, payload JSONB,
         UNIQUE (source, event_id))""",
    """CREATE TABLE billing_audit (
         id BIGSERIAL PRIMARY KEY,
         at TIMESTAMPTZ NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
         order_ref TEXT, customer_id BIGINT, detail JSONB)""",
    "CREATE INDEX billing_orders_customer ON billing_orders (customer_id, created_at)",
    "CREATE INDEX billing_orders_status ON billing_orders (status)",
    "CREATE INDEX billing_refunds_order ON billing_refunds (order_id)",
    "CREATE INDEX billing_events_order ON billing_gateway_events (order_ref, received_at)",
    "CREATE INDEX billing_audit_order ON billing_audit (order_ref, at)",
]

#: v2: cancelling from the customer's profile — the subscription stays active
#: until the end of the paid period, then ends (no grace); undone by "resume"
#: or by paying for another period.
_V2 = [
    "ALTER TABLE billing_subscriptions ADD COLUMN cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE billing_subscriptions ADD COLUMN cancel_requested_at TIMESTAMPTZ",
    "ALTER TABLE billing_subscriptions ADD COLUMN cancel_reason TEXT",
]

#: (version, statements). Append only; never edit one that has shipped.
MIGRATIONS = [(1, _V1), (2, _V2)]


def _connect():
    url = database_url()
    if url:
        return store._pg_connect(url), "postgres"
    if hosted():
        raise RuntimeError("BILLING_DATABASE_URL is not set")
    path = _sqlite_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=15, isolation_level=None)
    c.execute("PRAGMA foreign_keys = ON")
    return c, "sqlite"


def migrate(conn, dialect):
    cur = conn.cursor()
    if dialect == "postgres":
        cur.execute("SELECT pg_advisory_lock(727301)")
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS billing_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        if dialect == "postgres":
            conn.commit()
        cur.execute("SELECT version FROM billing_schema_migrations")
        done = {r[0] for r in cur.fetchall()}
        for version, stmts in MIGRATIONS:
            if version in done:
                continue
            if dialect == "sqlite":
                cur.execute("BEGIN IMMEDIATE")
            for s in stmts:
                cur.execute(s if dialect == "postgres" else _lite(s))
            cur.execute(_q("INSERT INTO billing_schema_migrations (version, applied_at) VALUES (%s, %s)", dialect),
                        (version, iso(now())))
            conn.commit() if dialect == "postgres" else cur.execute("COMMIT")
    except Exception:
        if dialect == "postgres":
            conn.rollback()
        else:
            with contextlib.suppress(sqlite3.Error):
                cur.execute("ROLLBACK")
        raise
    finally:
        if dialect == "postgres":
            cur.execute("SELECT pg_advisory_unlock(727301)")
            conn.commit()


def _q(sql, dialect):
    if dialect == "sqlite":
        sql = sql.replace("%s", "?").replace(" FOR UPDATE", "")
    return sql


class Tx:
    """One transaction: tx.all(...), tx.one(...), tx.run(...); `FOR UPDATE`
    locks rows on PostgreSQL (SQLite takes the whole database: BEGIN IMMEDIATE)."""
    def __init__(self, conn, dialect):
        self.conn, self.dialect, self.cur = conn, dialect, conn.cursor()

    def run(self, sql, params=()):
        self.cur.execute(_q(sql, self.dialect), tuple(_param(p) for p in params))
        return self.cur

    def all(self, sql, params=()):
        cur = self.run(sql, params)
        cols = [d[0] for d in cur.description]
        return [{c: _plain(v) for c, v in zip(cols, r)} for r in cur.fetchall()]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def insert(self, sql, params=()):
        """An INSERT ... ; the new row's id."""
        if self.dialect == "postgres":
            return self.all(sql + " RETURNING id", params)[0]["id"]
        return self.run(sql, params).lastrowid


def _param(p):
    if isinstance(p, Decimal):
        return str(p) if p is not None else None
    if isinstance(p, dt.datetime):
        return iso(p)
    if isinstance(p, (dict, list)):
        return json.dumps(p, separators=(",", ":"), default=str)
    return p


def _plain(v):
    if isinstance(v, dt.datetime):
        return iso(v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc))
    return v


@contextlib.contextmanager
def tx():
    conn, dialect = _connect()
    try:
        key = database_url() or _sqlite_path()
        if key not in _ready:
            with _lock:
                if key not in _ready:
                    migrate(conn, dialect)
                    _ready.add(key)
        t = Tx(conn, dialect)
        if dialect == "sqlite":
            t.cur.execute("BEGIN IMMEDIATE")
        try:
            yield t
        except BaseException:
            conn.rollback() if dialect == "postgres" else t.cur.execute("ROLLBACK")
            raise
        conn.commit() if dialect == "postgres" else t.cur.execute("COMMIT")
    finally:
        conn.close()


def read(sql, params=()):
    with tx() as t:
        return t.all(sql, params)


def reset():
    """Every billing table, gone — the tests' fresh start on a scratch database. Never
    called by the application."""
    names = ("billing_audit", "billing_gateway_events", "billing_refunds", "billing_invoices", "billing_orders",
             "billing_subscriptions", "billing_customers", "billing_plans", "billing_schema_migrations")
    conn, dialect = _connect()
    try:
        cur = conn.cursor()
        for n in names:
            cur.execute(f"DROP TABLE IF EXISTS {n}" + (" CASCADE" if dialect == "postgres" else ""))
        conn.commit()
    finally:
        conn.close()
    _ready.clear()
