"""Re-render the demo's recorded Customer return (/pos/returns/) and Alteration
(/pos/alterations/) landing pages from the CURRENT templates, the same way
rerender_store_dashboard.py does for the dashboard.

The recent credit notes and the open alteration jobs are read back out of the
recordings, so the demo lists exactly what was recorded.

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\rerender_store_billflows.py
"""
import datetime as dt
import html
import json
import re
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from rerender_store_dashboard import MANIFEST, SHOP, money


def u(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).split() and " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def tbody_rows(page):
    if "<tbody>" not in page:
        return []
    tb = page[page.index("<tbody>"):page.index("</tbody>")]
    return [re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S) for tr in re.findall(r"<tr>(.*?)</tr>", tb, re.S)]


def recent_returns(page):
    out = []
    for tds in tbody_rows(page):
        if len(tds) < 4 or "href" not in tds[0]:
            continue
        nid = int(re.search(r"/pos/returns/(\d+)", tds[0]).group(1))
        out.append(NS(id=nid, number=u(tds[0]), invoice=NS(invoice_number=u(tds[1])),
                      created_at=dt.datetime.strptime(u(tds[2]), "%d %b %Y, %H:%M"), total=money(u(tds[3]))))
    return out


def open_jobs(page):
    out = []
    for tds in tbody_rows(page):
        if len(tds) < 7 or "href" not in tds[0]:
            continue
        aid = int(re.search(r"/pos/alterations/(\d+)", tds[0]).group(1))
        prom = re.match(r"(\d{1,2} \w{3})", u(tds[4]) or "")
        cust = u(tds[2])
        out.append(NS(id=aid, number=u(tds[0]),
                      invoice=NS(invoice_number=u(tds[1]), customer=None if cust == "Walk-in" else NS(name=cust)),
                      tailor=None if u(tds[3]) in ("—", "") else NS(name=u(tds[3])),
                      promised_date=dt.datetime.strptime("2026 " + prom.group(1), "%Y %d %b") if prom else None,
                      is_overdue="overdue" in tds[4], status="ready" if "ready" in tds[5] else "pending"))
    return out


def url_for(endpoint, **kw):
    return {"returns.list_notes": "/pos/returns/notes",
            "returns.view_note": f"/pos/returns/{kw.get('nid')}",
            "alterations.list_jobs": "/pos/alterations/list",
            "alterations.tailors": "/pos/alterations/tailors",
            "alterations.view_job": f"/pos/alterations/{kw.get('aid')}",
            "alterations.mark_ready": f"/pos/alterations/{kw.get('aid')}/ready"}.get(endpoint, "/pos/")


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}{% block scripts %}{% endblock %}"}),
        FileSystemLoader(str(SHOP / "templates"))]), autoescape=True)
    env.filters["inr"] = lambda v: f"₹{float(v or 0):,.2f}"
    env.globals.update(url_for=url_for)
    user = NS(is_manager=True)
    done = 0
    for path, tpl, ctx in (("/pos/returns/", "returns/index.html", lambda p: dict(recent=recent_returns(p))),
                           ("/pos/alterations/", "alterations/index.html", lambda p: dict(open_jobs=open_jobs(p)))):
        for e in manifest["entries"]:
            if e["path"] != path or "text/html" not in (e.get("type") or "") or "body" not in e:
                continue
            page = e.setdefault("wx_original", e["body"])
            content = env.get_template(tpl).render(q="", invoice=None, current_user=user, **ctx(page))
            start = page.index('<div class="page-head">')
            end = page.index("</main>")
            e["body"] = page[:start] + content + "\n" + page[end:]
            done += 1
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"re-rendered {done} return / alteration page(s)")


if __name__ == "__main__":
    main()
