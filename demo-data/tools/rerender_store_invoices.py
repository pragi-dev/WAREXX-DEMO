"""Re-render the demo's recorded Store invoices page (/pos/pos/invoices) from the
CURRENT template, the same way rerender_store_dashboard.py does for the dashboard.

Every bill row, the filter lists and the three totals are read back out of the
recording. The recorded page is kept beside it (<file>.orig) and is always the
source, so this can be run again after any template change.

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\rerender_store_invoices.py
"""
import datetime as dt
import html
import json
import re
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from rerender_store_dashboard import DEMO, MANIFEST, SHOP, money

PATH = "/pos/pos/invoices"
BODIES = DEMO / "recorded" / "bodies"


def u(s):
    return html.unescape(re.sub(r"\s+", " ", s)).strip()


def rows_of(page):
    tbody = page[page.index("<tbody>"):page.index("</tbody>")]
    out = []
    for tr in re.findall(r"<tr class=\"[^\"]*\">(.*?)</tr>", tbody, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 8:
            continue
        num = re.search(r"<code>([^<]+)</code>", tds[0]).group(1)
        cancelled = "cancelled" in tds[0]
        floor = u(re.sub(r"<[^>]+>", "", tds[1]))
        when = dt.datetime.strptime(u(tds[2]), "%d %b %Y %H:%M")
        cust = None
        if "<br>" in tds[3]:
            name, rest = tds[3].split("<br>", 1)
            cust = NS(name=u(name), phone=u(re.sub(r"<[^>]+>", "", rest)))
        pay = u(re.sub(r"<[^>]+>", "", tds[5])).replace(" ", "_") or None
        iid = int(re.search(r"/pos/pos/invoice/(\d+)", tds[7]).group(1))
        out.append(NS(id=iid, invoice_number=u(num), is_cancelled=cancelled, cancel_reason="",
                      floor=NS(name=floor) if floor and floor != "—" else None, invoice_date=when,
                      customer=cust, staff=NS(full_name=u(tds[4])), cashier=None,
                      payment_method=pay, total=money(u(re.sub(r"<[^>]+>", "", tds[6])))))
    return out


def options_of(page, name):
    m = re.search(r'name="%s">(.*?)</select>' % name, page, re.S)
    return [(html.unescape(v), u(lbl)) for v, lbl in re.findall(r'<option value="([^"]*)"[^>]*>([^<]+)</option>', m.group(1)) if v] if m else []


def url_for(endpoint, **kw):
    return {"pos.invoice_list": PATH, "pos.counter": "/pos/pos/",
            "pos.view_invoice": f"/pos/pos/invoice/{kw.get('iid')}",
            "pos.print_invoice": f"/pos/pos/invoice/{kw.get('iid')}/print"}.get(endpoint, "/pos/")


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = sorted({e["file"] for e in manifest["entries"] if e["path"] == PATH and e.get("file")})
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(str(SHOP / "templates"))]), autoescape=True)
    env.filters["inr"] = lambda v: f"₹{float(v or 0):,.2f}"
    env.globals.update(url_for=url_for)
    for f in files:
        live, orig = BODIES / f, BODIES / (f + ".orig")
        if not orig.exists():
            orig.write_text(live.read_text(encoding="utf-8"), encoding="utf-8")
        page = orig.read_text(encoding="utf-8")
        vals = re.findall(r'<div class="value">([^<]+)</div>', page)
        summary = NS(count=int(vals[0]), total=money(vals[1]), tax=money(vals[2]))
        cashiers = [NS(id=int(v), full_name=l) for v, l in options_of(page, "cashier")]
        content = env.get_template("pos/invoices.html").render(
            invoices=rows_of(page), summary=summary, cashiers=cashiers,
            series_options=options_of(page, "series"), q="", date_from="", date_to="",
            cashier_id=None, payment="", status="", series="", min_amt="", max_amt="")
        start = page.index('<div class="page-head">')
        end = page.index("</main>")
        live.write_text(page[:start] + content + "\n" + page[end:], encoding="utf-8")
    print(f"re-rendered {len(files)} invoices page file(s)")


if __name__ == "__main__":
    main()
