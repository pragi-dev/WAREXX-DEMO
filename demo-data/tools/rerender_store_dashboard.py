"""Re-render the demo's recorded Store dashboard from the CURRENT template.

The demo (landing-demo/frontend/demo) answers /pos/ with a page recorded off a real server.
When templates/dashboard.html changes, the recording still shows the old page —
so this reads the figures back out of the recorded page, renders the new
template with them, and writes the result into demo-data/recorded/manifest.json. It
also records the dashboard's warehouse picture (static/img/warehouse.svg).

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\rerender_store_dashboard.py

Re-recording the whole demo (tools/record.mjs) makes this unnecessary.
"""
import datetime as dt
import html
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
SHOP = DEMO.parent.parent / "app" / "Textile Retail Shop" / "app"
MANIFEST = DEMO / "recorded" / "manifest.json"
SPLIT = "<!--wx-split-->"


def load_modules():
    spec = importlib.util.spec_from_file_location("shop_modules", SHOP / "modules.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [NS(**m) for m in mod.MODULES]


def money(s):
    return float(s.replace("₹", "").replace(",", ""))


def figures(page):
    vals = re.findall(r'<div class="kpi-value">([^<]+)</div>', page)
    if len(vals) < 6:
        raise SystemExit("the recorded page is not the old dashboard — nothing to read back")
    today, week, month, low, products, customers = vals[:6]
    invoices = []
    for iid, num, cust, cashier, total, when in re.findall(
            r'invoice/(\d+)"><code>([^<]+)</code></a></td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*'
            r'<td[^>]*>([^<]+)</td>\s*<td[^>]*>([^<]+)</td>', page):
        invoices.append(NS(id=int(iid), invoice_number=html.unescape(num),
                           customer=NS(name=html.unescape(cust)) if cust != "Walk-in" else None,
                           cashier=NS(full_name=html.unescape(cashier)), total=money(total),
                           invoice_date=dt.datetime.strptime("2026 " + when.strip(), "%Y %d %b %H:%M")))
    low_items = [NS(name=html.unescape(n), stock_qty=q, unit=u) for n, q, u in re.findall(
        r'<span class="text-truncate">([^<]+)</span>\s*<span class="badge text-bg-warning">(\S+) ([^<]+)</span>', page)]
    labels = json.loads(re.search(r"labels: (\[[^\]]*\])", page).group(1))
    values = json.loads(re.search(r"data: (\[[^\]]*\])", page).group(1))
    user = html.unescape(re.search(r'<span class="user-name">([^<]+)</span>', page).group(1))
    shop = html.unescape(re.search(r'<h4 class="page-title">([^<]+)</h4>', page).group(1))
    return dict(today_sales=money(today), week_sales=money(week), month_sales=money(month),
                low_stock=int(low), total_products=int(products), total_customers=int(customers),
                recent_invoices=invoices, low_stock_items=low_items, trend_labels=labels,
                trend_values=values, current_user=NS(full_name=user, username=user), SHOP_NAME=shop)


ROUTES = {"pos.counter": "/pos/pos/", "pos.invoice_list": "/pos/pos/invoices",
          "reports.index": "/pos/reports/", "customers.list_customers": "/pos/customers/",
          "inventory.list_products": "/pos/inventory/"}


def url_for(endpoint, **kw):
    if endpoint == "static":
        return "/pos/static/" + kw["filename"]
    if endpoint == "pos.view_invoice":
        return f"/pos/pos/invoice/{kw['iid']}"
    return ROUTES[endpoint]


def render(ctx, modules):
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}" + SPLIT + "{% block scripts %}{% endblock %}"}),
        FileSystemLoader(str(SHOP / "templates"))]), autoescape=True)
    env.filters["inr"] = lambda v: f"₹{float(v or 0):,.2f}"
    env.globals.update(url_for=url_for, SHOP_MODULES=modules)
    for m in modules:
        ROUTES.setdefault(m.endpoint, None)
    return env.get_template("dashboard.html").render(**ctx).split(SPLIT)


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    modules = load_modules()
    pages = [e for e in manifest["entries"] if e["path"] == "/pos/" and "text/html" in (e.get("type") or "")]
    # endpoints of the module cards, read off the recorded menu: label -> href
    first = pages[0]["body"]
    hrefs = dict((html.unescape(lbl), href) for href, lbl in re.findall(
        r'<a class="qa-card mod-card" href="([^"]+)">.*?<span class="qa-title"><span>([^<]+)</span>', first, re.S))
    for m in modules:
        ROUTES[m.endpoint] = hrefs.get(m.label, "/pos/")
    for e in pages:
        page = e["body"]
        if "sx-hero" in page:            # already re-rendered: read the figures from the original copy
            page = e.get("wx_original", page)
        e.setdefault("wx_original", page)
        content, scripts = render(figures(page), modules)
        start = page.index('<div class="page-head">')
        end = page.index("</main>")
        body = page[:start] + content + "\n" + page[end:]
        s0 = body.index("<script>\nnew Chart(")
        s1 = body.index("</script>", s0) + len("</script>")
        e["body"] = body[:s0] + scripts.strip() + body[s1:]
    css = next(e for e in manifest["entries"] if e["path"] == "/pos/static/css/app.css")
    css["body"] = (SHOP / "static" / "css" / "app.css").read_text(encoding="utf-8")
    # every picture in the shop's static/img (the dashboard's warehouse, the logo):
    # SVG inline like the stylesheet, PNG as a file under data/bodies/
    types = {".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp"}
    for f in sorted((SHOP / "static" / "img").iterdir()):
        if f.suffix not in types:
            continue
        path = "/pos/static/img/" + f.name
        e = next((x for x in manifest["entries"] if x["path"] == path), None)
        if e is None:
            e = {k: css[k] for k in css if k not in ("body", "file")}
            e.update(key=css["key"].replace("/pos/static/css/app.css", path), path=path)
            manifest["entries"].append(e)
        e["type"] = types[f.suffix]
        e.pop("body", None), e.pop("file", None)
        if f.suffix == ".svg":
            e["body"] = f.read_text(encoding="utf-8")
        else:
            name = "shop-" + f.name
            (DEMO / "recorded" / "bodies" / name).write_bytes(f.read_bytes())
            e["file"] = name
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"re-rendered {len(pages)} store dashboard page(s)")


if __name__ == "__main__":
    main()
