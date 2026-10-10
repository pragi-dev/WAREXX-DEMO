"""Where the landing site keeps what visitors send: every form (leads), every
free trial (trials) and what happened to it — email sent, signed up, signed in,
tour finished or skipped. Read back by the admin page (admin.py).

A separate database from the WAREXX software's: this one holds the website's
visitors and nothing else, and nothing here can reach the production data.

Which database:
    LEADS_DATABASE_URL, else POSTGRES_URL (what Vercel Postgres / Neon sets),
    else DATABASE_URL — a postgres:// address, used through pg8000 (pure Python).
    None of them set (a local machine): a SQLite file, lead-api/data/leads.db
    (LEADS_SQLITE_PATH to move it), so development and the tests need nothing.

Never in the way: every write is wrapped so that a database that is down, slow
or misconfigured is logged and skipped — a visitor's form, email and demo carry
on exactly as before. Nothing is logged about the visitor themselves.
"""
import datetime as dt
import os
import sqlite3
import ssl
import threading
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
_lock = threading.Lock()
_ready = set()                     # databases whose tables have been made in this process


def database_url():
    for name in ("LEADS_DATABASE_URL", "POSTGRES_URL", "DATABASE_URL"):
        v = (os.environ.get(name) or "").strip()
        if v.startswith(("postgres://", "postgresql://")):
            return v
    return ""


def kind():
    return "postgres" if database_url() else "sqlite"


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ---- connections ---------------------------------------------------------------

def _pg_connect(url):
    import pg8000.dbapi              # only needed when a Postgres address is set
    u = urlparse(url)
    q = parse_qs(u.query)
    mode = (q.get("sslmode") or ["require" if u.hostname not in ("localhost", "127.0.0.1") else "disable"])[0]
    ctx = None
    if mode != "disable":
        ctx = ssl.create_default_context()
        if mode in ("require", "prefer", "allow"):      # encrypted, as the providers' own drivers do
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    return pg8000.dbapi.connect(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                host=u.hostname, port=u.port or 5432,
                                database=(u.path or "/").lstrip("/") or "postgres",
                                ssl_context=ctx, timeout=10)


def _sqlite_path():
    return os.environ.get("LEADS_SQLITE_PATH") or os.path.join(HERE, "data", "leads.db")


def connect():
    url = database_url()
    if url:
        return _pg_connect(url), "postgres"
    path = _sqlite_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    return c, "sqlite"


_SCHEMA = {
    "postgres": [
        """CREATE TABLE IF NOT EXISTS leads (
             id BIGSERIAL PRIMARY KEY, created_at TEXT NOT NULL, type TEXT, name TEXT, email TEXT,
             phone TEXT, company TEXT, industry TEXT, interest TEXT, message TEXT, slot TEXT,
             page TEXT, trial_ref TEXT, email_sent INTEGER)""",
        """CREATE TABLE IF NOT EXISTS trials (
             ref TEXT PRIMARY KEY, created_at TEXT NOT NULL, email TEXT NOT NULL, name TEXT,
             company TEXT, phone TEXT, window_start BIGINT, window_end BIGINT, window_text TEXT,
             email_sent INTEGER DEFAULT 0, emails_sent INTEGER DEFAULT 0, signed_up_at TEXT,
             last_login_at TEXT, logins INTEGER DEFAULT 0, tour_state TEXT, tour_at TEXT)""",
        "CREATE INDEX IF NOT EXISTS leads_created ON leads (created_at)",
        "CREATE INDEX IF NOT EXISTS leads_email ON leads (email)",
        "CREATE INDEX IF NOT EXISTS trials_email ON trials (email)",
    ],
    "sqlite": [
        """CREATE TABLE IF NOT EXISTS leads (
             id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, type TEXT, name TEXT,
             email TEXT, phone TEXT, company TEXT, industry TEXT, interest TEXT, message TEXT,
             slot TEXT, page TEXT, trial_ref TEXT, email_sent INTEGER)""",
        """CREATE TABLE IF NOT EXISTS trials (
             ref TEXT PRIMARY KEY, created_at TEXT NOT NULL, email TEXT NOT NULL, name TEXT,
             company TEXT, phone TEXT, window_start INTEGER, window_end INTEGER, window_text TEXT,
             email_sent INTEGER DEFAULT 0, emails_sent INTEGER DEFAULT 0, signed_up_at TEXT,
             last_login_at TEXT, logins INTEGER DEFAULT 0, tour_state TEXT, tour_at TEXT)""",
        "CREATE INDEX IF NOT EXISTS leads_created ON leads (created_at)",
        "CREATE INDEX IF NOT EXISTS leads_email ON leads (email)",
        "CREATE INDEX IF NOT EXISTS trials_email ON trials (email)",
    ],
}


def _sql(q, dialect):
    """Queries are written with %s; SQLite wants ?."""
    return q.replace("%s", "?") if dialect == "sqlite" else q


def run(query, params=(), fetch=False):
    """One statement (or query) on a fresh connection: serverless functions keep
    nothing between requests, and a long-lived local server is no busier."""
    conn, dialect = connect()
    try:
        key = database_url() or _sqlite_path()
        if key not in _ready:
            with _lock:
                cur = conn.cursor()
                for stmt in _SCHEMA[dialect]:
                    cur.execute(stmt)
                conn.commit()
                _ready.add(key)
        cur = conn.cursor()
        cur.execute(_sql(query, dialect), tuple(params))
        rows = None
        if fetch:
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


def _quiet(fn):
    """A write that must never break the visitor's request."""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as exc:                      # noqa: BLE001 — logged, the visitor carries on
            print(f"[store] {fn.__name__} skipped: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
            return None
    wrapper.__name__ = fn.__name__
    return wrapper


# ---- writes (the lead API) -------------------------------------------------------

@_quiet
def record_lead(lead, trial_ref=None, email_sent=None):
    run("""INSERT INTO leads (created_at, type, name, email, phone, company, industry, interest,
                              message, slot, page, trial_ref, email_sent)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (_now(), lead.get("type"), lead.get("name"), (lead.get("email") or "").lower(), lead.get("phone"),
         lead.get("company"), lead.get("industry"), lead.get("interest"), lead.get("message"),
         lead.get("slot"), (lead.get("page") or "")[:500], trial_ref,
         None if email_sent is None else int(bool(email_sent))))
    return True


@_quiet
def record_trial(ref, lead, start, end, window, email_sent):
    run("""INSERT INTO trials (ref, created_at, email, name, company, phone, window_start, window_end,
                               window_text, email_sent, emails_sent)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (ref, _now(), (lead.get("email") or "").lower(), lead.get("name"), lead.get("company"),
         lead.get("phone"), int(start), int(end), window, int(bool(email_sent)), int(bool(email_sent))))
    return True


@_quiet
def trial_email(ref, sent):
    """Another attempt at the trial's email (a retry or a resend)."""
    if sent:
        run("UPDATE trials SET email_sent = 1, emails_sent = emails_sent + 1 WHERE ref = %s", (ref,))
    return True


@_quiet
def trial_signed_up(ref):
    run("UPDATE trials SET signed_up_at = %s WHERE ref = %s AND signed_up_at IS NULL", (_now(), ref))
    return True


@_quiet
def trial_logged_in(ref):
    run("UPDATE trials SET last_login_at = %s, logins = logins + 1 WHERE ref = %s", (_now(), ref))
    return True


@_quiet
def trial_tour(ref, state):
    run("UPDATE trials SET tour_state = %s, tour_at = %s WHERE ref = %s", (state, _now(), ref))
    return True


@_quiet
def tour_state(ref):
    rows = run("SELECT tour_state FROM trials WHERE ref = %s", (ref,), fetch=True)
    return rows[0]["tour_state"] if rows else None


# ---- reads (the admin page) -----------------------------------------------------

LEAD_COLS = ("id", "created_at", "type", "name", "email", "phone", "company", "industry", "interest",
             "message", "slot", "page", "trial_ref", "email_sent")
TRIAL_COLS = ("ref", "created_at", "email", "name", "company", "phone", "window_text", "window_start",
              "window_end", "email_sent", "emails_sent", "signed_up_at", "last_login_at", "logins",
              "tour_state", "tour_at")


def leads(q="", type_="", limit=50, offset=0):
    where, params = [], []
    if type_:
        where.append("type = %s"); params.append(type_)
    if q:
        like = "%" + q.lower() + "%"
        where.append("(LOWER(COALESCE(name,'')) LIKE %s OR LOWER(COALESCE(email,'')) LIKE %s "
                     "OR LOWER(COALESCE(company,'')) LIKE %s OR COALESCE(phone,'') LIKE %s)")
        params += [like, like, like, "%" + q + "%"]
    w = (" WHERE " + " AND ".join(where)) if where else ""
    total = run("SELECT COUNT(*) AS n FROM leads" + w, params, fetch=True)[0]["n"]
    rows = run(f"SELECT {', '.join(LEAD_COLS)} FROM leads{w} ORDER BY created_at DESC, id DESC "
               f"LIMIT {int(limit)} OFFSET {int(offset)}", params, fetch=True)
    return {"total": int(total), "rows": rows}


def trials(q="", status="", limit=50, offset=0):
    where, params = [], []
    if q:
        like = "%" + q.lower() + "%"
        where.append("(LOWER(COALESCE(name,'')) LIKE %s OR LOWER(email) LIKE %s OR LOWER(COALESCE(company,'')) LIKE %s)")
        params += [like, like, like]
    if status == "signed_up":
        where.append("signed_up_at IS NOT NULL")
    elif status == "not_signed_up":
        where.append("signed_up_at IS NULL")
    elif status == "email_failed":
        where.append("email_sent = 0")
    w = (" WHERE " + " AND ".join(where)) if where else ""
    total = run("SELECT COUNT(*) AS n FROM trials" + w, params, fetch=True)[0]["n"]
    rows = run(f"SELECT {', '.join(TRIAL_COLS)} FROM trials{w} ORDER BY created_at DESC "
               f"LIMIT {int(limit)} OFFSET {int(offset)}", params, fetch=True)
    return {"total": int(total), "rows": rows}


def summary():
    one = lambda q: int(run(q, (), fetch=True)[0]["n"])  # noqa: E731
    return {"leads": one("SELECT COUNT(*) AS n FROM leads"),
            "trials": one("SELECT COUNT(*) AS n FROM trials"),
            "signed_up": one("SELECT COUNT(*) AS n FROM trials WHERE signed_up_at IS NOT NULL"),
            "tour_completed": one("SELECT COUNT(*) AS n FROM trials WHERE tour_state = 'completed'"),
            "email_failed": one("SELECT COUNT(*) AS n FROM trials WHERE email_sent = 0"),
            "database": kind()}
