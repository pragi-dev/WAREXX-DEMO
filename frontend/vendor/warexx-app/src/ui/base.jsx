// Shared list controls: paging, search, filters, minimizable panels.
// Moved out of App.jsx so every screen draws them from one place.
import React, { useEffect, useRef, useState } from 'react'
import { Icon } from './icons.jsx'

export const PAGE_SIZES = [25, 50, 100, 200]

// A kept-open screen (App's dashboard stash) refreshes when it is shown again —
// not while hidden, and not on its first showing (it has just loaded).
export function useReshow(active, reload) {
  const was = useRef(active)
  const fn = useRef(reload)
  fn.current = reload
  useEffect(() => {
    if (active && !was.current) fn.current()
    was.current = active
  }, [active])
}
// how many dashboards (per warehouse) stay open at once
export const STASH_MAX = 8

export function usePaged(rows, initial = 50) {
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(initial)
  const list = rows || []
  const total = list.length
  const pages = size === 0 ? 1 : Math.max(1, Math.ceil(total / size))
  // A filter that shortens the list must not strand you on page 9 of 3 looking
  // at an empty screen — which reads as "the search found nothing".
  useEffect(() => { if (page > pages) setPage(1) }, [pages, page])
  const start = size === 0 ? 0 : (page - 1) * size
  return {
    page, setPage, size, setSize, total, pages,
    from: total === 0 ? 0 : start + 1,
    to: size === 0 ? total : Math.min(total, start + size),
    slice: size === 0 ? list : list.slice(start, start + size),
  }
}

export function Pager({ page, setPage, size, setSize, total, pages, from, to,
                 noun = 'row', nouns, style }) {
  // Nothing to page through, and no page-size choice worth offering: a bar under
  // a list of four is noise. The screens carry their own counts already.
  if (total <= PAGE_SIZES[0]) return null
  const go = (p) => setPage(Math.min(pages, Math.max(1, p)))
  // a short run of page numbers around the current one, with the ends kept
  const nums = []
  for (let p = 1; p <= pages; p++) {
    if (p === 1 || p === pages || Math.abs(p - page) <= 1) nums.push(p)
    else if (nums[nums.length - 1] !== '…') nums.push('…')
  }
  return (
    <div className="pager wx-pager" style={style}>
      <span className="pager-count">
        <b>{from.toLocaleString('en-IN')}–{to.toLocaleString('en-IN')}</b>
        {' of '}<b>{total.toLocaleString('en-IN')}</b>{' '}
        {total === 1 ? noun : (nouns || noun + 's')}
      </span>
      <label className="pager-size">
        Rows
        <select value={size} onChange={(e) => { setSize(+e.target.value); setPage(1) }}>
          {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          <option value={0}>All</option>
        </select>
      </label>
      <nav className="pager-nav" aria-label="Pages">
        <button className="pg" disabled={page <= 1} onClick={() => go(page - 1)} title="Previous page" aria-label="Previous page">
          <Icon name="arrowLeft" size={14} /></button>
        {pages > 1 && nums.map((p, i) => p === '…'
          ? <span key={'g' + i} className="pg gap">…</span>
          : <button key={p} className={'pg' + (p === page ? ' on' : '')} aria-current={p === page ? 'page' : undefined}
              onClick={() => go(p)}>{p}</button>)}
        <button className="pg" disabled={page >= pages} onClick={() => go(page + 1)} title="Next page" aria-label="Next page">
          <Icon name="arrowRight" size={14} /></button>
      </nav>
    </div>
  )
}

export function SearchBox({ value, onChange, placeholder, style, title }) {
  return (
    <div className="searchbox" style={style}
      title={title || 'Search within what is shown. Esc clears it.'}>
      <span className="searchicon"><Icon name="search" size={14} /></span>
      <input value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder || 'Search…'}
        onKeyDown={(e) => { if (e.key === 'Escape' && value) { e.preventDefault(); onChange('') } }} />
      {value && <button className="searchclear" title="Clear the search (Esc)"
        onClick={() => onChange('')}>×</button>}
    </div>
  )
}

// Mutually exclusive scopes — the common filter, and the one worth showing
// open rather than behind a button. `options`: [value, label, count?, tooltip?]
export function FilterChips({ value, onChange, options, title }) {
  return (
    <div className="chiprow" title={title || 'Filter which records are listed'}>
      {options.map(([v, label, count, tip]) => (
        <button key={v} className={'fchip' + (value === v ? ' on' : '')}
          title={tip || `Show ${String(label).toLowerCase()}`}
          onClick={() => onChange(v)}>
          {label}{count != null && <span className="n">{count}</span>}
        </button>
      ))}
    </div>
  )
}

// Anything richer than a chip row lives behind this: a labelled disclosure that
// states how many filters are on, and can always be cleared in one click.
export function FilterButton({ open, onToggle, active, title }) {
  return (
    <button className={'filterbtn' + (open || active ? ' on' : '')} onClick={onToggle}
      title={title || (active
        ? `${active} filter${active === 1 ? '' : 's'} active — click to change or clear`
        : 'Filter which records are listed')}>
      <Icon name="sliders" size={14} /> Filters
      {active > 0 && <span className="count">{active}</span>}
    </button>
  )
}

export function FilterPanel({ open, active, onClear, onApply, children, hint }) {
  if (!open) return null
  return (
    <div className="filterpanel">
      <div className="row">{children}</div>
      <div className="filterfoot">
        <span className="small" style={{ color: 'var(--muted)' }}>
          {hint || (active ? `${active} filter${active === 1 ? '' : 's'} active` : 'No filters set — everything is listed')}
        </span>
        <div style={{ flex: 1 }} />
        {onApply && <button className="btn primary" onClick={onApply} title="Run with these filters">Apply</button>}
        <button className="btn" onClick={onClear} disabled={!active}
          title={active ? 'Remove every filter' : 'Nothing to clear'}>Clear all</button>
      </div>
    </div>
  )
}

// The state behind one Filters panel: its values, open or shut, and how many are
// set — counted the same way on every screen, so the badge on the button means
// the same thing wherever it is read.
export function useFilters() {
  const [open, setOpen] = useState(false)
  const [f, setAll] = useState({})
  const set = (k, v) => setAll((x) => ({ ...x, [k]: v }))
  const active = Object.values(f).filter((v) => v !== '' && v != null).length
  return { open, toggle: () => setOpen((o) => !o), f, set, clear: () => setAll({}), active }
}

// The distinct non-blank values of one field, sorted — the options a Filters
// dropdown offers, drawn from the records actually on the list so it never
// offers a choice that can only come back empty.
export const uniqOf = (rows, key) => [...new Set((rows || []).map((r) => r?.[key])
  .filter((v) => v != null && String(v).trim() !== '').map(String))]
  .sort((a, b) => a.localeCompare(b))

// A Filters dropdown: "Any" plus the given options ([value, label] or a value).
export function FilterSelect({ label, value, onChange, options, anyLabel = 'Any', style }) {
  return (
    <div className="field" style={style}><label>{label}</label>
      <select value={value ?? ''} onChange={(e) => onChange(e.target.value)}>
        <option value="">{anyLabel}</option>
        {options.map((o) => {
          const [v, l] = Array.isArray(o) ? o : [o, o]
          return <option key={v} value={v}>{l}</option>
        })}
      </select>
    </div>
  )
}

// Search and Filters on one row at the top of a sidebar list — the same pair
// every full-width screen has, fitted to a 300px column.
export function SideTools({ q, setQ, placeholder, flt }) {
  return (
    <div className="sidetools">
      <SearchBox value={q} onChange={setQ} placeholder={placeholder} />
      <FilterButton open={flt.open} onToggle={flt.toggle} active={flt.active} />
    </div>
  )
}

// Collapsed panels are remembered per screen and survive a reload, because a
// warehouse screen is set up once for how someone works and then left alone.
export const MINI_KEY = 'essa_minimized'
export const readMinimized = () => {
  try { return JSON.parse(localStorage.getItem(MINI_KEY) || '{}') } catch { return {} }
}
export function useMinimized(id, defaultOpen = true) {
  const [open, setOpen] = useState(() => {
    const saved = readMinimized()[id]
    return saved === undefined ? defaultOpen : !saved
  })
  const toggle = () => setOpen((o) => {
    const next = !o
    try {
      const all = readMinimized()
      if (next === defaultOpen) delete all[id]; else all[id] = !next
      localStorage.setItem(MINI_KEY, JSON.stringify(all))
    } catch { /* private mode — the panel still toggles, it just won't persist */ }
    return next
  })
  return [open, toggle]
}

// The list down the left of most screens, with a collapse.
//
// Same three rules the panels keep: it slides away, it is remembered per screen
// across navigation and reloads, and collapsed it still says what it holds — the
// title and the count stay legible down the rail. A list that vanishes with no
// trace of itself is a missing feature, not a hidden one, and the way back has to
// be visible from where it went.
//
// The content stays mounted and is clipped rather than unmounted, so the width
// genuinely animates instead of the panel popping between two states. It is
// `visibility: hidden` while collapsed, which also takes it out of the tab order —
// a keyboard user should not travel through a list they cannot see.
export function Sidebar({ id, label, children, width }) {
  const [open, toggle] = useMinimized('side.' + id, true)
  return (
    <div className={'sidebar' + (open ? '' : ' collapsed')}
      style={width && open ? { width, minWidth: width } : undefined}>
      {open ? (
        <button className="sidehide" onClick={toggle}
          title={`Hide the ${label.toLowerCase()} list — the screen keeps this setting`}>«</button>
      ) : (
        <button className="siderail" onClick={toggle} title={`Show the ${label.toLowerCase()} list`}>
          <span className="chev" aria-hidden="true">»</span>
          <span className="raillabel">{label}</span>
        </button>
      )}
      <div className="sidebody" style={width ? { width, minWidth: width } : undefined}>{children}</div>
    </div>
  )
}

// A section with a minimize control. `summary` is what it says while collapsed —
// a minimized panel that doesn't say what it holds is just a missing panel.
export function Section({ id, title, summary, actions, children, defaultOpen = true, style }) {
  const [open, toggle] = useMinimized(id, defaultOpen)
  // "Order book · 6" — the count reads as a quiet pill beside the name
  const m = typeof title === 'string' ? title.match(/^(.*?)\s·\s([\d,]+)$/) : null
  return (
    <div className="section" style={style}>
      <div className={'panelhead' + (open ? '' : ' closed')} onClick={toggle}
        title={open ? 'Minimize this panel' : 'Expand this panel'}>
        <h4>{m ? <>{m[1]}<span className="panelcount">{m[2]}</span></> : title}</h4>
        {!open && summary ? <span className="panelsum">{summary}</span> : null}
        {actions && open ? <span className="panelacts" onClick={(e) => e.stopPropagation()}>{actions}</span> : null}
        {/* the fold control sits at the end of the band, where the eye finishes
            reading the title — a chevron that turns, not a −/+ box before it */}
        <button className="mini" aria-expanded={open} aria-label={open ? 'Minimize' : 'Expand'}
          title={open ? 'Minimize' : 'Expand'} onClick={(e) => { e.stopPropagation(); toggle() }}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m6 9 6 6 6-6" /></svg>
        </button>
      </div>
      {open && children}
    </div>
  )
}
