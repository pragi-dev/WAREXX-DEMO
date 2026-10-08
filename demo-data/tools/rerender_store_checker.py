"""Re-render the demo's recorded Stock check page (/pos/stock-check/) from the
CURRENT template, the same way rerender_store_dashboard.py does for the dashboard.

The filter lists (category, size, colour…) are read back out of the recorded
page, so the demo offers exactly what the recording offered.

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\rerender_store_checker.py
"""
import html
import json
import re
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from rerender_store_dashboard import MANIFEST, SHOP

PATH = "/pos/stock-check/"


def url_for(endpoint, **kw):
    if endpoint == "checker.index":
        return PATH + (f"?code={kw['code']}" if "code" in kw else "")
    return "/pos/"


def filters_of(page):
    filters, options = [], {}
    for label, name, body in re.findall(
            r'<label class="form-label mb-0 small">([^<]+)</label>\s*<select[^>]*name="(\w+)">(.*?)</select>', page, re.S):
        filters.append((name, html.unescape(label.strip())))
        options[name] = [html.unescape(v) for v in re.findall(r'<option value="([^"]+)"', body)]
    return filters, options


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pages = [e for e in manifest["entries"] if e["path"] == PATH and "text/html" in (e.get("type") or "")]
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(str(SHOP / "templates"))]), autoescape=True)
    env.globals.update(url_for=url_for)
    env.filters["inr"] = lambda v: f"₹{float(v or 0):,.2f}"
    for e in pages:
        page = e.setdefault("wx_original", e["body"])
        filters, options = filters_of(page)
        content = env.get_template("checker/index.html").render(
            code="", product=None, text="", filters=filters, options=options,
            selected={k: "" for k, _ in filters}, price_min=None, price_max=None,
            results=[], request=NS(args={}))
        start = page.index('<div class="page-head">')
        end = page.index("</main>")
        e["body"] = page[:start] + content + "\n" + page[end:]
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"re-rendered {len(pages)} stock-check page(s)")


if __name__ == "__main__":
    main()
