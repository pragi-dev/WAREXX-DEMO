"""Re-render the demo's recorded Floor Sales page (/pos/floor/) from the CURRENT
template, the same way rerender_store_dashboard.py does for the dashboard.

The open sessions are read back out of the recorded page (code, status, item
count, customer) and the new templates/floor/index.html is rendered with them.
The recording never carried a session's total, so the demo shows none.

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\rerender_store_floor.py
"""
import datetime as dt
import html
import json
import re
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from rerender_store_dashboard import MANIFEST, SHOP

PATH = "/pos/floor/"


def sessions_of(page):
    out = []
    for code, status, n, cust in re.findall(
            r'<strong>([A-Z0-9]+)</strong>\s*<span class="badge[^"]*">([^<]+)</span>\s*'
            r'<div class="small text-muted">(\d+) items · ([^<]+)</div>', page):
        cust = html.unescape(cust.strip())
        out.append(NS(code=code, status=status.strip(), items=[None] * int(n),
                      customer=None if cust == "Walk-in" else NS(name=cust),
                      created_at=None, totals=lambda: {"total": None}))
    # a page already re-rendered keeps its sessions in the cards
    for code, status, cust, n in re.findall(
            r'<span class="fs-code">([A-Z0-9]+)</span>.*?data-status="([^"]+)".*?'
            r'<i class="bi bi-person"></i> ([^<]+)</div>.*?<span>Items</span><b>(\d+)</b>', page, re.S):
        cust = html.unescape(cust.strip())
        out.append(NS(code=code, status=status, items=[None] * int(n),
                      customer=None if cust == "Walk-in customer" else NS(name=cust),
                      created_at=None, totals=lambda: {"total": None}))
    return out


def url_for(endpoint, **kw):
    if endpoint == "floor.new_session":
        return "/pos/floor/new"
    if endpoint == "floor.session_view":
        return f"/pos/floor/s/{kw['code']}"
    if endpoint == "static":
        return "/pos/static/" + kw["filename"]
    return "/pos/"


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pages = [e for e in manifest["entries"] if e["path"] == PATH and "text/html" in (e.get("type") or "")]
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(str(SHOP / "templates"))]), autoescape=True)
    env.filters["inr"] = lambda v: "—" if v is None else f"₹{float(v or 0):,.2f}"
    env.globals.update(url_for=url_for)
    for e in pages:
        page = e.setdefault("wx_original", e["body"])
        content = env.get_template("floor/index.html").render(sessions=sessions_of(page))
        start = page.index('<div class="page-head">')
        end = page.index("</main>")
        e["body"] = page[:start] + content + "\n" + page[end:]
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"re-rendered {len(pages)} floor page(s)")


if __name__ == "__main__":
    main()
