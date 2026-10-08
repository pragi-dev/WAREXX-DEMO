"""Re-render the demo's recorded Floors & tills page (/pos/stores/) from the
CURRENT template, the same way rerender_store_dashboard.py does for the dashboard.

Each store's floors (order, prefix, next bill, bills this year, tills on it) and
tills (floor they stand on) are read back out of the recording.

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\rerender_store_floors.py
"""
import html
import json
import re
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from rerender_store_dashboard import MANIFEST, SHOP

PATH = "/pos/stores/"


def txt(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def parse(page):
    year, example = re.search(r"Financial year <strong>(\d+)</strong> · e.g. <code>([^<]+)</code>", page).groups()
    blocks = re.split(r'<h5 class="mb-0"><i class="bi bi-shop"></i> ', page)[1:]
    locations, counts, series, fid = [], {}, {}, 0
    for bi, block in enumerate(blocks):
        name = txt(block[:block.index("</h5>")])
        loc = NS(id=bi + 1, name=name, floors=[], counters=[])
        tables = re.findall(r"<tbody>(.*?)</tbody>", block, re.S)
        floor_rows = re.findall(r"<tr>(.*?)</tr>", tables[0], re.S) if tables else []
        till_names = {}
        for row in floor_rows:
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if len(tds) < 7:
                continue
            fid += 1
            fname = txt(re.sub(r"<span.*", "", tds[1], flags=re.S))
            prefix = txt(tds[2]).strip("—").strip() or None
            nxt = txt(tds[3])
            m = re.match(r".*?(\d+)$", nxt)
            if prefix and m:
                series[(prefix, year)] = int(m.group(1)) - 1
            counts[fid] = int(txt(tds[4]) or 0)
            f = NS(id=fid, name=fname, prefix=prefix, sort_order=int(txt(tds[0]) or 0), active=True,
                   wh_id=1 if "warehouse" in tds[1] else None, counters=[], location=loc)
            for t in [x.strip() for x in txt(tds[5]).split(",") if x.strip() and "no till" not in x]:
                till_names[t] = f
            loc.floors.append(f)
        till_rows = re.findall(r"<tr>(.*?)</tr>", tables[1], re.S) if len(tables) > 1 else []
        for row in till_rows:
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if len(tds) < 4:
                continue
            cid = re.search(r"/counters/(\d+)/floor", row)
            tname = txt(re.sub(r"<span.*", "", tds[0], flags=re.S))
            floor = till_names.get(tname) or next((f for f in loc.floors if f.name == txt(tds[1])), None)
            t = NS(id=int(cid.group(1)) if cid else 0, name=tname, active=True,
                   wh_id=1 if "warehouse" in tds[0] else None, floor=floor, floor_id=floor.id if floor else None)
            loc.counters.append(t)
            if floor:
                floor.counters.append(t)
        locations.append(loc)
    return dict(year=year, example=example, locations=locations, counts=counts, series=series,
                unassigned=sum(1 for l in locations for t in l.counters if not t.floor), shared={}, wh_available=True)


def url_for(endpoint, **kw):
    return {"stores.series": "/pos/stores/series"}.get(endpoint, PATH)


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(str(SHOP / "templates"))]), autoescape=True)
    env.globals.update(url_for=url_for)
    done = 0
    for e in manifest["entries"]:
        if e["path"] != PATH or "text/html" not in (e.get("type") or "") or "body" not in e:
            continue
        page = e.setdefault("wx_original", e["body"])
        content = env.get_template("stores/index.html").render(**parse(page))
        start = page.index('<div class="page-head">')
        end = page.index("</main>")
        e["body"] = page[:start] + content + "\n" + page[end:]
        done += 1
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"re-rendered {done} floors & tills page(s)")


if __name__ == "__main__":
    main()
