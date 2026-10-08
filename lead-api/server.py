"""The landing site's lead API as a small stand-alone server — for local
development, and for any host that can run a Python process (a VM, a container,
Render, Railway...). On Vercel the same code runs as ../api/leads.py instead.

    python lead-api/server.py                 (http://127.0.0.1:8003/api/leads)
    LEAD_API_PORT=9000 LEAD_API_HOST=0.0.0.0 python lead-api/server.py

Standard library only — nothing to install. Settings: see lead_mail.py.
"""
import os
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http_handler import LeadHandler  # noqa: E402
import lead_mail  # noqa: E402


def main():
    host = os.environ.get("LEAD_API_HOST", "127.0.0.1")
    port = int(os.environ.get("LEAD_API_PORT", "8003"))
    srv = ThreadingHTTPServer((host, port), LeadHandler)
    where = "SMTP " + lead_mail.SMTP_HOST if lead_mail.MAIL_BACKEND == "smtp" else "saved to " + lead_mail.MAIL_DIR
    print(f"[lead api] http://{host}:{port}/api/leads  (mail: {where})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
