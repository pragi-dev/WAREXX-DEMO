// Bring the demo's recorded Store reports page (/pos/reports/) up to the current
// templates/reports/index.html markup for the parts the catalogue view shows: the
// ask bar, the group headings and the report tiles. Works on the recording kept
// in `wx_original`, so it can be run again.
//
//     node landing-demo/demo-data/tools/restyle_store_reports.mjs      (from the repo root)
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const here = path.dirname(fileURLToPath(import.meta.url))
const mf = path.join(here, '..', 'recorded', 'manifest.json')
const M = JSON.parse(fs.readFileSync(mf, 'utf8'))
const ICON = { sales: 'graph-up-arrow', analysis: 'bar-chart-line', stock: 'box-seam', discount: 'tag', settlement: 'wallet2',
  tax: 'percent', hr: 'people', b2b: 'briefcase', customer: 'person-heart' }
const title = (s) => s.toLowerCase().replace(/(^|[\s-])(\w)/g, (m, a, b) => a + b.toUpperCase()).replace(/\bB2b\b/, 'B2B').replace(/\bHr\b/, 'HR')

let pages = 0
for (const e of M.entries) {
  if (e.path !== '/pos/reports/' || !(e.type || '').includes('text/html') || !e.body) continue
  if (!e.wx_original) e.wx_original = e.body
  let s = e.wx_original
  // the ask bar
  s = s.replace(/<div class="card p-3 mb-3" style="border-color:rgba\(201,82,15,\.3\); background:var\(--brand-50\);">\s*<div class="d-flex align-items-center gap-2 flex-wrap">[\s\S]*?<button id="askGo" class="btn btn-brand">Ask<\/button>\s*<\/div>/,
    `<div class="rx-ask mb-3">
  <div class="rx-ask-bar">
    <span class="rx-ask-ico"><i class="bi bi-stars"></i></span>
    <input id="askBox" class="form-control" placeholder="Ask a question — “what did we sell last month”, “கடந்த மாதம் விற்பனை எவ்வளவு”" autocomplete="off">
    <button id="askMic" class="btn rx-mic" type="button" title="Speak the question"><i class="bi bi-mic"></i></button>
    <div class="btn-group rx-lang" role="group">
      <input type="radio" class="btn-check" name="asklang" id="langEn" value="en-IN" checked>
      <label class="btn btn-sm" for="langEn">EN</label>
      <input type="radio" class="btn-check" name="asklang" id="langTa" value="ta-IN">
      <label class="btn btn-sm" for="langTa">தமிழ்</label>
    </div>
    <button id="askGo" class="btn btn-brand">Ask</button>
  </div>`)
  // group headings
  s = s.replace(/(<section class="rpt-group" data-group="([^"]+)">\s*<button type="button" class="rpt-head" aria-expanded="true">\s*)<span>([^<]+?) <span class="rpt-n">\((\d+)\)<\/span><\/span>/g,
    (m, pre, key, label, n) => `${pre}<span class="rx-gico"><i class="bi bi-${ICON[key] || 'folder2'}"></i></span>
        <span class="rx-glabel">${title(label)}</span><span class="rpt-n">${n}</span>`)
  // report tiles
  s = s.replace(/(<span>[^<]*<\/span>)\s*<span class="rpt-na">not recorded<\/span>(\s*<\/a>)/g, '$1<span class="rpt-na">Not recorded</span>$2')
  s = s.replace(/(<i class="bi bi-file-earmark-bar-graph"><\/i>\s*<span>[^<]*<\/span>)(\s*<\/a>)/g, '$1<i class="bi bi-arrow-right rx-go"></i>$2')
  s = s.split("head.querySelector('.bi')").join("head.querySelector(':scope > .bi')")
  e.body = s
  pages++
}
fs.writeFileSync(mf, JSON.stringify(M))
console.log(`restyled ${pages} reports page(s)`)
