"""Re-render the demo's recorded Promotion schemes page (/pos/promotions/) from the
CURRENT template, the same way rerender_store_dashboard.py does for the dashboard.

Every scheme row (priority, name, code, offer, dates, stores, bills, given away,
status) is read back out of the recording. "Today" is the day the demo was
recorded, so the run meters agree with the recorded statuses.

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\rerender_store_promotions.py
"""
import datetime as dt
import html
import json
import re
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from rerender_store_dashboard import MANIFEST, SHOP, money

PATH = "/pos/promotions/"
RECORDED_ON = dt.date(2026, 10, 5)


def txt(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def day(s):
    try:
        return dt.datetime.strptime(s.strip(), "%d-%m-%Y").date()
    except ValueError:
        return None


def parse(page):
    tb = page[page.index("<tbody>"):page.index("</tbody>")]
    schemes, used, given, offers = [], {}, {}, {}
    for tr in re.findall(r"<tr>(.*?)</tr>", tb, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 9:
            continue
        sid = int(re.search(r"/pos/promotions/(\d+)", tds[1]).group(1))
        name = txt(re.search(r"<a[^>]*>(.*?)</a>", tds[1], re.S).group(1))
        code = txt(re.search(r"<code[^>]*>(.*?)</code>", tds[1], re.S).group(1))
        dates = [x for x in re.split(r"<br>\s*to", tds[3])]
        start = day(txt(dates[0]))
        end = day(txt(dates[1])) if len(dates) > 1 else None
        places = [NS(label=txt(p)) for p in re.split(r"<br>", tds[4]) if txt(p) and txt(p) != "everywhere"]
        state = {"switched off": "inactive"}.get(txt(tds[7]), txt(tds[7]))
        used[sid], given[sid], offers[sid] = int(txt(tds[5]) or 0), money(txt(tds[6])), txt(tds[2])
        schemes.append(NS(id=sid, priority=int(txt(tds[0]) or 0), name=name, code=code,
                          stackable="stacks" in tds[1], start_date=start, end_date=end, places=places,
                          active=state != "inactive", status=(lambda st: (lambda today: st))(state)))
    counts = {}
    for s in schemes:
        k = s.status(None)
        counts[k] = counts.get(k, 0) + 1
    return dict(schemes=schemes, used=used, given=given, counts=counts,
                describe=lambda s: offers[s.id], today=RECORDED_ON, status="", q="")


def url_for(endpoint, **kw):
    sid = kw.get("sid")
    return {"coupons.index": "/pos/coupons/", "promotions.new_scheme": PATH + "new",
            "promotions.index": PATH, "promotions.detail": f"{PATH}{sid}",
            "promotions.edit_scheme": f"{PATH}{sid}/edit", "promotions.toggle": f"{PATH}{sid}/toggle"}.get(endpoint, "/pos/")


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(str(SHOP / "templates"))]), autoescape=True)
    env.filters["inr"] = lambda v: f"₹{float(v or 0):,.2f}"
    env.filters["dmy"] = lambda d: d.strftime("%d-%m-%Y") if d else ""
    env.globals.update(url_for=url_for)
    done = 0
    for e in manifest["entries"]:
        if e["path"] != PATH or "text/html" not in (e.get("type") or "") or "body" not in e:
            continue
        page = e.setdefault("wx_original", e["body"])
        content = env.get_template("promotions/list.html").render(**parse(page))
        start = page.index('<div class="page-head">')
        end = page.index("</main>")
        e["body"] = page[:start] + content + "\n" + page[end:]
        done += 1
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"re-rendered {done} promotions page(s)")


if __name__ == "__main__":
    main()
