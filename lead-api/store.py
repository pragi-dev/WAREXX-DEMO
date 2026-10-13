"""Where the landing site keeps what visitors send: every person (contacts), every
form (leads), every free trial (trials) and what happened to it — each email
(email_log), each sign-in attempt (signin_log), signed up, tour finished or
skipped. Read back by the admin page (admin.py). The tables are listed above _SCHEMA.

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


#: The demo's own database. On PostgreSQL every table is typed and constrained:
#:   contacts     one row per person (email, unique, lower-case): the latest details
#:                they gave, when first and last seen, how many forms they sent
#:   leads        every form submission, linked to its contact (and its trial)
#:   trials       every booked demo slot: the slot (epoch seconds + timestamptz),
#:                the emailed password's salted hash (never the password), sign-up,
#:                sign-ins, the tour
#:   email_log    every trial email the lead API tried to send: recipient, outcome
#:   signin_log   every email + password sign-in attempt: which trial, ok or why not
#: plus the view demo_users: one row per trial with its contact and live status.
#: SQLite (local fallback, the tests) keeps the same tables, loosely typed.
_FORM_TYPES = "'contact','demo','brochure','sandbox','prebook','plan','partner'"
_SCHEMA = {
    "postgres": [
        """CREATE TABLE IF NOT EXISTS contacts (
             id BIGSERIAL PRIMARY KEY,
             email TEXT NOT NULL UNIQUE CHECK (email = lower(email) AND email LIKE '%_@_%'),
             name TEXT, phone TEXT, company TEXT, industry TEXT,
             first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             forms INTEGER NOT NULL DEFAULT 0 CHECK (forms >= 0))""",
        """CREATE TABLE IF NOT EXISTS trials (
             ref TEXT PRIMARY KEY,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             contact_id BIGINT REFERENCES contacts (id) ON DELETE SET NULL,
             email TEXT NOT NULL CHECK (email = lower(email)),
             name TEXT, company TEXT, phone TEXT,
             window_start BIGINT NOT NULL,
             window_end BIGINT NOT NULL,
             slot_start TIMESTAMPTZ GENERATED ALWAYS AS (to_timestamp(window_start)) STORED,
             slot_end TIMESTAMPTZ GENERATED ALWAYS AS (to_timestamp(window_end)) STORED,
             window_text TEXT,
             email_sent INTEGER NOT NULL DEFAULT 0 CHECK (email_sent IN (0, 1)),
             emails_sent INTEGER NOT NULL DEFAULT 0 CHECK (emails_sent >= 0),
             pw_salt TEXT, pw_hash TEXT, password_issued_at TIMESTAMPTZ,
             signed_up_at TIMESTAMPTZ, last_login_at TIMESTAMPTZ,
             logins INTEGER NOT NULL DEFAULT 0 CHECK (logins >= 0),
             tour_state TEXT CHECK (tour_state IN ('completed', 'skipped')), tour_at TIMESTAMPTZ,
             CHECK (window_end > window_start),
             CHECK ((pw_salt IS NULL) = (pw_hash IS NULL)))""",
        f"""CREATE TABLE IF NOT EXISTS leads (
             id BIGSERIAL PRIMARY KEY,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
             contact_id BIGINT REFERENCES contacts (id) ON DELETE SET NULL,
             type TEXT NOT NULL CHECK (type IN ({_FORM_TYPES})),
             name TEXT, email TEXT NOT NULL CHECK (email = lower(email)),
             phone TEXT, company TEXT, industry TEXT, interest TEXT, message TEXT,
             slot TEXT, page TEXT, trial_ref TEXT,
             email_sent INTEGER CHECK (email_sent IN (0, 1)))""",
        """CREATE TABLE IF NOT EXISTS email_log (
             id BIGSERIAL PRIMARY KEY,
             at TIMESTAMPTZ NOT NULL DEFAULT now(),
             kind TEXT NOT NULL CHECK (kind IN ('trial', 'resend')),
             recipient TEXT NOT NULL,
             trial_ref TEXT REFERENCES trials (ref) ON DELETE CASCADE,
             with_password BOOLEAN NOT NULL DEFAULT FALSE,
             status TEXT NOT NULL CHECK (status IN ('sent', 'saved', 'failed')))""",
        """CREATE TABLE IF NOT EXISTS signin_log (
             id BIGSERIAL PRIMARY KEY,
             at TIMESTAMPTZ NOT NULL DEFAULT now(),
             email TEXT NOT NULL,
             trial_ref TEXT,
             ok BOOLEAN NOT NULL,
             reason TEXT)""",
        "CREATE INDEX IF NOT EXISTS leads_created ON leads (created_at)",
        "CREATE INDEX IF NOT EXISTS leads_email ON leads (email)",
        "CREATE INDEX IF NOT EXISTS leads_contact ON leads (contact_id)",
        "CREATE INDEX IF NOT EXISTS leads_trial ON leads (trial_ref)",
        "CREATE INDEX IF NOT EXISTS trials_email ON trials (email)",
        "CREATE INDEX IF NOT EXISTS trials_contact ON trials (contact_id)",
        "CREATE INDEX IF NOT EXISTS trials_slot ON trials (window_start)",
        "CREATE INDEX IF NOT EXISTS email_log_trial ON email_log (trial_ref)",
        "CREATE INDEX IF NOT EXISTS signin_log_email ON signin_log (email, at)",
        """CREATE OR REPLACE VIEW demo_users AS
             SELECT t.ref, c.id AS contact_id, t.email, COALESCE(t.name, c.name) AS name,
                    COALESCE(t.company, c.company) AS company, COALESCE(t.phone, c.phone) AS phone,
                    c.industry, t.slot_start, t.slot_end, t.window_text,
                    CASE WHEN now() < t.slot_start THEN 'upcoming'
                         WHEN now() < t.slot_end THEN 'active' ELSE 'expired' END AS status,
                    t.pw_hash IS NOT NULL AS password_emailed, t.password_issued_at,
                    t.emails_sent, t.signed_up_at, t.last_login_at, t.logins, t.tour_state, t.created_at
               FROM trials t LEFT JOIN contacts c ON c.id = t.contact_id""",
    ],
    "sqlite": [
        """CREATE TABLE IF NOT EXISTS contacts (
             id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, name TEXT, phone TEXT,
             company TEXT, industry TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
             forms INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS leads (
             id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, type TEXT, name TEXT,
             email TEXT, phone TEXT, company TEXT, industry TEXT, interest TEXT, message TEXT,
             slot TEXT, page TEXT, trial_ref TEXT, email_sent INTEGER)""",
        """CREATE TABLE IF NOT EXISTS trials (
             ref TEXT PRIMARY KEY, created_at TEXT NOT NULL, email TEXT NOT NULL, name TEXT,
             company TEXT, phone TEXT, window_start INTEGER, window_end INTEGER, window_text TEXT,
             email_sent INTEGER DEFAULT 0, emails_sent INTEGER DEFAULT 0, signed_up_at TEXT,
             last_login_at TEXT, logins INTEGER DEFAULT 0, tour_state TEXT, tour_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS email_log (
             id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
             recipient TEXT NOT NULL, trial_ref TEXT, with_password INTEGER NOT NULL DEFAULT 0,
             status TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS signin_log (
             id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, email TEXT NOT NULL,
             trial_ref TEXT, ok INTEGER NOT NULL, reason TEXT)""",
        "CREATE INDEX IF NOT EXISTS leads_created ON leads (created_at)",
        "CREATE INDEX IF NOT EXISTS leads_email ON leads (email)",
        "CREATE INDEX IF NOT EXISTS trials_email ON trials (email)",
    ],
}


#: Columns added after the first release, for SQLite files made before them. SQLite
#: has no ADD COLUMN IF NOT EXISTS: each is tried, and "duplicate column" is fine.
#: pw_salt / pw_hash: the emailed password's salted hash (trial.py, DEMO_EMAIL_PASSWORD)
#: — never the password, and never shown on the admin page.
_SQLITE_ADD = ["ALTER TABLE trials ADD COLUMN pw_salt TEXT", "ALTER TABLE trials ADD COLUMN pw_hash TEXT",
               "ALTER TABLE trials ADD COLUMN password_issued_at TEXT",
               "ALTER TABLE trials ADD COLUMN contact_id INTEGER",
               "ALTER TABLE leads ADD COLUMN contact_id INTEGER"]

#: Every table and view, dependents first (reset()).
_ALL = ("VIEW demo_users", "TABLE email_log", "TABLE signin_log", "TABLE leads", "TABLE trials",
        "TABLE contacts")


def _sql(q, dialect):
    """Queries are written with %s; SQLite wants ?."""
    return q.replace("%s", "?") if dialect == "sqlite" else q


def _plain(v):
    """Timestamps come back from PostgreSQL as datetimes: ISO text, as SQLite keeps
    them, so the admin page's JSON and the code reading them see one kind."""
    if isinstance(v, dt.datetime):
        return v.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
    return v


def reset():
    """Every table, gone: the tests' fresh start on a scratch database."""
    pg = kind() == "postgres"
    for what in _ALL:
        typ, name = what.split()
        if pg or typ == "TABLE":
            run(f"DROP {typ} IF EXISTS {name}" + (" CASCADE" if pg else ""))
    _ready.clear()


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
                if dialect == "sqlite":
                    for stmt in _SQLITE_ADD:
                        try:
                            cur.execute(stmt)
                        except sqlite3.OperationalError:          # already there
                            pass
                conn.commit()
                _ready.add(key)
        cur = conn.cursor()
        cur.execute(_sql(query, dialect), tuple(params))
        rows = None
        if fetch:
            cols = [d[0] for d in cur.description]
            rows = [{c: _plain(v) for c, v in zip(cols, r)} for r in cur.fetchall()]
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

def _blank(v):
    v = (v or "").strip() if isinstance(v, str) else v
    return v or None


def _contact(lead, count=True):
    """The person behind a form: one row per email, kept up to date with the
    latest details they gave (a blank field never wipes an earlier answer)."""
    email = (lead.get("email") or "").strip().lower()
    if not email:
        return
    now = _now()
    run("""INSERT INTO contacts (email, name, phone, company, industry, first_seen_at, last_seen_at, forms)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (email) DO UPDATE SET
             name = COALESCE(excluded.name, contacts.name), phone = COALESCE(excluded.phone, contacts.phone),
             company = COALESCE(excluded.company, contacts.company),
             industry = COALESCE(excluded.industry, contacts.industry),
             last_seen_at = excluded.last_seen_at, forms = contacts.forms + excluded.forms""",
        (email, _blank(lead.get("name")), _blank(lead.get("phone")), _blank(lead.get("company")),
         _blank(lead.get("industry")), now, now, 1 if count else 0))


_CONTACT_ID = "(SELECT id FROM contacts WHERE email = %s)"


@_quiet
def record_lead(lead, trial_ref=None, email_sent=None):
    email = (lead.get("email") or "").strip().lower()
    _contact(lead)
    run(f"""INSERT INTO leads (created_at, contact_id, type, name, email, phone, company, industry, interest,
                               message, slot, page, trial_ref, email_sent)
            VALUES (%s, {_CONTACT_ID}, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (_now(), email, lead.get("type"), lead.get("name"), email, lead.get("phone"),
         lead.get("company"), lead.get("industry"), lead.get("interest"), lead.get("message"),
         lead.get("slot"), (lead.get("page") or "")[:500], trial_ref,
         None if email_sent is None else int(bool(email_sent))))
    return True


@_quiet
def record_trial(ref, lead, start, end, window, email_sent, pw=None):
    """pw: (salt, hash) of an emailed password, or None. True once kept."""
    salt, hash_ = pw or (None, None)
    email = (lead.get("email") or "").strip().lower()
    _contact(lead, count=False)                       # the form itself is counted by record_lead
    run(f"""INSERT INTO trials (ref, created_at, contact_id, email, name, company, phone, window_start,
                                window_end, window_text, email_sent, emails_sent, pw_salt, pw_hash,
                                password_issued_at)
            VALUES (%s, %s, {_CONTACT_ID}, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (ref, _now(), email, email, lead.get("name"), lead.get("company"),
         lead.get("phone"), int(start), int(end), window, int(bool(email_sent)), int(bool(email_sent)),
         salt, hash_, _now() if hash_ else None))
    return True


@_quiet
def trial_password(ref, pw):
    """A new emailed password for a trial (a resend): the old one stops working."""
    run("UPDATE trials SET pw_salt = %s, pw_hash = %s, password_issued_at = %s WHERE ref = %s",
        (pw[0], pw[1], _now(), ref))
    return True


@_quiet
def log_email(kind_, recipient, trial_ref, sent, with_password, backend="console"):
    """One trial email, as it went: sent (SMTP), saved (.eml file, console mode) or failed."""
    status = ("sent" if backend == "smtp" else "saved") if sent else "failed"
    run("""INSERT INTO email_log (at, kind, recipient, trial_ref, with_password, status)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (_now(), kind_, (recipient or "").strip().lower(), trial_ref, bool(with_password), status))
    return True


@_quiet
def log_signin(email, trial_ref, ok, reason=None):
    """One email + password sign-in attempt: the password itself is never kept."""
    run("INSERT INTO signin_log (at, email, trial_ref, ok, reason) VALUES (%s, %s, %s, %s, %s)",
        (_now(), str(email or "").strip().lower()[:320], trial_ref, bool(ok), reason))
    return True


def trial_logins(email):
    """The trials an email + password sign-in is checked against, newest first.
    Not _quiet: a database that is down must say so, not answer "wrong password"."""
    return run("""SELECT ref, window_start, window_end, pw_salt, pw_hash FROM trials
                  WHERE email = %s AND pw_hash IS NOT NULL ORDER BY created_at DESC LIMIT 20""",
               (str(email or "").strip().lower(),), fetch=True)


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
    return {"contacts": one("SELECT COUNT(*) AS n FROM contacts"),
            "emails_logged": one("SELECT COUNT(*) AS n FROM email_log"),
            "signins_failed": one("SELECT COUNT(*) AS n FROM signin_log WHERE NOT ok"),
            "leads": one("SELECT COUNT(*) AS n FROM leads"),
            "trials": one("SELECT COUNT(*) AS n FROM trials"),
            "signed_up": one("SELECT COUNT(*) AS n FROM trials WHERE signed_up_at IS NOT NULL"),
            "tour_completed": one("SELECT COUNT(*) AS n FROM trials WHERE tour_state = 'completed'"),
            "email_failed": one("SELECT COUNT(*) AS n FROM trials WHERE email_sent = 0"),
            "database": kind()}
