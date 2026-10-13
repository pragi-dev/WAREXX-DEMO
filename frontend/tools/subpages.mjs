// /legal: the landing page with <main> holding a page of its own
// (landing/extras/legal-page.html) — the same head, header, footer and
// scripts, so the header and footer work there as on every other page.
// Used by tools/landing.mjs (build and dev server).
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const EXTRAS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'landing', 'extras')

export const SUBPAGES = {
  // the policies: Privacy, Terms, Refunds, Disclaimer — linked in the footer
  legal: { tpl: 'legal-page.html', title: 'Legal policies — WAREXX', keep: ['top', 'main', 'legal', 'privacy', 'terms', 'refunds', 'disclaimer'] },
}

export function subPage(landingHtml, name) {
  const p = SUBPAGES[name]
  const open = '<main id="main">'
  const a = landingHtml.indexOf(open), b = landingHtml.indexOf('</main>')
  if (a < 0 || b < 0) throw new Error(`landing/index.html: no <main> to build /${name} from`)
  const body = readFileSync(path.join(EXTRAS, p.tpl), 'utf8')
  let html = landingHtml.slice(0, a + open.length) + '\n' + body + '\n' + landingHtml.slice(b)
  html = html.replace(/<html([^>]*)>/i, (_, attrs) => `<html${attrs} data-page="${name}">`)
  html = html.replace(/<title>[^<]*<\/title>/i, () => `<title>${p.title}</title>`)
  // the landing page's section links lead back to it; this page's own stay
  const keep = new RegExp('href="#(?!(?:' + p.keep.join('|') + ')")([A-Za-z][\w-]*)"', 'g')
  return html.replace(keep, (_, id) => `href="/#${id}"`)
}

/** The policies, linked in the footer of every page. */
export function withLegalLinks(html) {
  const nav = /(<nav class="foot__links"[^>]*>[\s\S]*?)(<\/nav>)/
  if (!nav.test(html) || html.includes('href="/legal/#terms"')) return html
  return html.replace(nav, (_, links, end) => links
    + '<a href="/legal/#terms">Terms</a><a href="/legal/#privacy">Privacy</a>'
    + '<a href="/legal/#refunds">Refunds</a><a href="/legal/#disclaimer">Disclaimer</a>' + end)
}
