"""Re-render the demo's recorded Delivery page (/pos/delivery/) from the CURRENT
template, the same way rerender_store_dashboard.py does for the dashboard.

Only the markup above the manager-override dialog is replaced; the recorded
page's script — which drives the scanning — is kept exactly as recorded.

    app\\backend\\.venv\\Scripts\\python.exe landing-demo\\demo-data\\tools\\rerender_store_delivery.py
"""
import html
import json
import re
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from rerender_store_dashboard import MANIFEST, SHOP

PATH = "/pos/delivery/"
MODAL = '<div class="modal fade" id="overrideModal"'


def url_for(endpoint, **kw):
    return {"delivery.pending": "/pos/delivery/pending",
            "delivery.list_deliveries": "/pos/delivery/list"}.get(endpoint, "/pos/")


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pages = [e for e in manifest["entries"] if e["path"] == PATH and "text/html" in (e.get("type") or "")]
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(str(SHOP / "templates"))]), autoescape=True)
    env.globals.update(url_for=url_for)
    for e in pages:
        page = e.setdefault("wx_original", e["body"])
        today = re.search(r'DATE</div>\s*<div class="col-8">([^<]+)</div>', page)
        loc = re.search(r'id="placeLocationName">([^<]*)<', page)
        ctr = re.search(r'id="placeCounterName">([^<]*)<', page)
        name = lambda m: (lambda v: None if v in ("", "—") else NS(name=v))(html.unescape(m.group(1).strip())) if m else None
        content = env.get_template("delivery/index.html").render(
            today=today.group(1).strip() if today else "", chosen_location=name(loc), chosen_counter=name(ctr))
        new_top = content[:content.index(MODAL)]
        start = page.index('<div class="page-head">')
        end = page.index(MODAL)
        e["body"] = page[:start] + new_top + page[end:]
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"re-rendered {len(pages)} delivery page(s)")


if __name__ == "__main__":
    main()
