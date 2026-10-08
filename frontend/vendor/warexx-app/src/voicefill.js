/**
 * Turning one spoken sentence into several filled fields.
 *
 * A master record is 30–52 boxes and most of them are one word. Dictating them
 * one box at a time is barely faster than typing; the win is saying the whole
 * row — "name cotton shirt hsn 6205 sales tax 5 margin min 10" — and having it
 * land in five fields.
 *
 * **Labels are the grammar.** There is no attempt at natural language: the
 * form's OWN labels are the delimiters. Every label the speaker actually said
 * is located in the transcript, and the value of a field is whatever was said
 * between its label and the next one. That is why it works without punctuation,
 * which matters because a speech recogniser hands back "name cotton shirt hsn
 * 6205" with no commas at all — and why it needs no dictionary per master: a
 * master that grows a field grows a spoken name for it at the same moment.
 *
 * **Longest label first.** "Margin (Min)" and "Margin (Max)" both contain
 * "margin". Claiming the longest phrase first, and refusing overlaps, is what
 * stops "margin min 10" from being read as the field "Margin" with the value
 * "min 10".
 *
 * Kept out of App.jsx and free of JSX so it can be run directly against a list
 * of real phrases — the matching is the part that will be wrong, and the only
 * way to know is to try it on sentences people would actually say.
 */

/**
 * Lowercase, strip punctuation, collapse spaces. Both sides go through this.
 *
 * Two things must survive, and both were losses found by testing:
 *
 *   * **Combining marks.** Tamil vowel signs are \p{M}, not \p{L}, so keeping
 *     only letters turned "பருத்தி சட்டை" into "பர த த சட ட" — the words taken
 *     apart into their consonants.
 *   * **Punctuation inside a number.** A dot or comma between two digits is part
 *     of the figure; anywhere else it is speech punctuation. Stripping both
 *     alike read "12.5" as 12 and "1,250" as 1.
 */
export function normaliseSpoken(s) {
  return String(s == null ? '' : s)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\p{M}.,]+/gu, ' ')
    // now drop every dot and comma that is NOT between two digits
    .replace(/(?<!\d)[.,]|[.,](?!\d)/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

//: abbreviations a label writes and a person says in full
const QUALIFIERS = { min: 'minimum', max: 'maximum', qty: 'quantity', no: 'number' }

/** Every spoken name a field answers to, longest first. */
export function fieldPhrases(f) {
  const out = new Set()
  const label = normaliseSpoken(f.label)
  if (label) out.add(label)
  // Two groups can carry the SAME label — the Employee master has a "City" in
  // Present address and another in Permanent address, and nine such pairs. The
  // bare label can only ever reach the first, so the group is offered as a
  // qualifier ("permanent address city", and the shorter "permanent city"),
  // which is how somebody would say it anyway when both exist.
  if (f._group && label) {
    const group = normaliseSpoken(f._group)
    if (group && group !== label) {
      out.add(`${group} ${label}`)
      const head = group.split(' ')[0]
      if (head && head !== group) out.add(`${head} ${label}`)
    }
  }
  // "Margin (Min)" is said as often as "minimum margin" — accept the words in
  // either order, and in full as well as abbreviated. Without the expansion,
  // "minimum margin" matched neither of the qualified phrases and fell through
  // to the bare "margin", which both Min and Max answer to: the value then
  // landed on whichever of them the form happened to list first.
  const paren = /^(.*?)\s*\((.+?)\)\s*$/.exec(String(f.label || ''))
  if (paren) {
    const [, head, qual] = paren
    const quals = [qual, QUALIFIERS[normaliseSpoken(qual)]].filter(Boolean)
    quals.forEach((q) => {
      out.add(normaliseSpoken(`${head} ${q}`))
      out.add(normaliseSpoken(`${q} ${head}`))
    })
    out.add(normaliseSpoken(head))
  }
  // the key as spoken ("gst_rate" → "gst rate"), which is often the shorter,
  // more natural name for a label written out in full
  const key = normaliseSpoken(String(f.key || '').replace(/_/g, ' '))
  if (key) out.add(key)
  ;(f.aliases || []).forEach((a) => { const n = normaliseSpoken(a); if (n) out.add(n) })
  return [...out].filter(Boolean).sort((a, b) => b.length - a.length)
}

/**
 * The fields a dictation may fill — every typed box and every checkbox.
 *
 * Returned as copies carrying `_group` (the section they sit in) and `_dup` (the
 * label is not unique in this master). Copies because these annotations are for
 * matching and must not be written onto the definition the form is rendering
 * from. Child grids are absent: a dictated sentence has no way to say which row
 * of a rate table it means.
 */
export function dictationTargets(def) {
  const out = []
  ;(def?.groups || []).forEach((g) => (g.fields || []).forEach((f) => {
    out.push({ ...f, _group: g.title })
  }))
  const seen = {}
  out.forEach((f) => { const k = normaliseSpoken(f.label); seen[k] = (seen[k] || 0) + 1 })
  out.forEach((f) => { f._dup = seen[normaliseSpoken(f.label)] > 1 })
  return out
}

const NUMBER = /-?\d+(?:[.,]\d+)*/
const YES = ['yes', 'on', 'true', 'tick', 'ticked', 'enable', 'enabled', 'check', 'checked', 'y']
const NO = ['no', 'off', 'false', 'untick', 'disable', 'disabled', 'uncheck', 'unchecked', 'n']

/** A spoken value, turned into what the field actually stores. */
export function coerceSpoken(f, spoken) {
  const raw = String(spoken || '').trim()
  if (f.type === 'check') {
    const words = normaliseSpoken(raw).split(' ')
    if (words.some((w) => NO.includes(w))) return false
    // "dumping" said with nothing after it is someone ticking it, not clearing it
    return true
  }
  if (!raw) return null
  if (f.type === 'num' || f.type === 'money') {
    const m = NUMBER.exec(raw)
    if (!m) return null
    // "1,250.50" — commas group, the dot is the point. Said units ("10 percent",
    // "5 rupees") fall outside the match and are dropped with it.
    const n = Number(m[0].replace(/,/g, ''))
    return Number.isFinite(n) ? n : null
  }
  if (f.options && f.options.length) {
    // snap to the master's own vocabulary, so a dictated value is a value the
    // record can actually hold. Exact first, then contained either way round.
    const said = normaliseSpoken(raw)
    const opts = f.options.map((o) => [o, normaliseSpoken(o)])
    const exact = opts.find(([, n]) => n === said)
    if (exact) return exact[0]
    const near = opts.find(([, n]) => n && (n.includes(said) || said.includes(n)))
    if (near) return near[0]
    return raw          // not in the list: keep what was said, the box accepts it
  }
  return raw
}

/**
 * Find every field named in the transcript and take its value from the words up
 * to the next named field.
 *
 * Returns { fills: [{field, value, spoken}], preamble, heard } — `preamble` is
 * anything said before the first recognised label, which is how a mishearing
 * announces itself instead of vanishing.
 */
export function parseDictation(def, transcript) {
  const heard = normaliseSpoken(transcript)
  if (!heard) return { fills: [], preamble: '', heard }
  const padded = ` ${heard} `

  // claim spans longest-phrase-first so "margin min" beats "margin"
  const candidates = []
  dictationTargets(def).forEach((f) => {
    fieldPhrases(f).forEach((phrase) => candidates.push({ f, phrase }))
  })
  candidates.sort((a, b) => b.phrase.length - a.phrase.length)

  const claimed = []            // {start, end, field} over `padded`
  const taken = new Set()       // one span per field — the first time it is named
  const overlaps = (s, e) => claimed.some((c) => s < c.end && e > c.start)
  for (const { f, phrase } of candidates) {
    if (taken.has(f.key)) continue
    const needle = ` ${phrase} `
    let from = 0
    for (;;) {
      const at = padded.indexOf(needle, from)
      if (at < 0) break
      const start = at + 1                       // inside the padding
      const end = start + phrase.length
      if (!overlaps(start, end)) {
        claimed.push({ start, end, field: f })
        taken.add(f.key)
        break
      }
      from = at + 1
    }
  }
  claimed.sort((a, b) => a.start - b.start)

  // A value can be the name of another field: the Trade Agreement master has a
  // "Supplier" field AND a Party Type whose options are Supplier/Agent/Buyer, so
  // "party type supplier" claimed both labels and left both without a value.
  // Where a field is left empty and the label that follows it is one of its own
  // options — and that label has no value of its own — the label was the value.
  const consumed = new Set()
  claimed.forEach((c, i) => {
    const next = claimed[i + 1]
    if (!next || consumed.has(i)) return
    const between = padded.slice(c.end, next.start).trim()
    if (between) return                       // it had a value; nothing to fix
    const after = padded.slice(next.end, i + 2 < claimed.length ? claimed[i + 2].start : padded.length).trim()
    if (after) return                         // the next label owns a value of its own
    const said = padded.slice(next.start, next.end).trim()
    const opts = (c.field.options || []).map((o) => normaliseSpoken(o))
    if (opts.includes(said)) { c.spokenOverride = said; consumed.add(i + 1) }
  })

  const fills = []
  claimed.forEach((c, i) => {
    if (consumed.has(i)) return
    const stop = i + 1 < claimed.length ? claimed[i + 1].start : padded.length
    const spoken = c.spokenOverride || padded.slice(c.end, stop).trim()
    const value = coerceSpoken(c.field, spoken)
    // a typed field with nothing said after its name was a label read out with
    // no value — skip it rather than blanking whatever is already in the box
    if (value === null || value === '') return
    fills.push({ field: c.field, value, spoken })
  })

  const preamble = claimed.length ? padded.slice(1, claimed[0].start).trim() : heard
  return { fills, preamble, heard }
}
