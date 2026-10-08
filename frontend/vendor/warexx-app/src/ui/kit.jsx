// ==========================================================================
//  WAREXX UI kit
//  ------------------------------------------------------------------------
//  The pieces every screen is composed from. A screen is:
//
//    PageHeader     where you are, what this is, the main actions
//    Kpis           the operational summary (one strip, not a row of cards)
//    DataPanel      the workspace: a toolbar, the table, the pager
//    Drawer         detail and advanced filters, from the right
//    EmptyState / LoadingState / ErrorState   one state system
//    FormSection / ActionBar                  grouped forms, sticky actions
//
//  Styling lives in ./warexx.css under the `wx-` prefix.
// ==========================================================================
import React, { createContext, useContext, useEffect } from 'react'
import { Icon } from './icons.jsx'

// The shell tells every page where it is standing: workspace, place, module.
export const PageCtx = createContext({ crumbs: [], group: '', icon: null })
export const usePage = () => useContext(PageCtx)

const cx = (...a) => a.filter(Boolean).join(' ')

export function Crumbs({ items, className }) {
  const list = (items || []).filter(Boolean)
  if (!list.length) return null
  return (
    <nav className={cx('wx-crumbs', className)} aria-label="You are here">
      {list.map((c, i) => {
        const last = i === list.length - 1
        const label = typeof c === 'string' ? c : c.label
        const go = typeof c === 'string' ? null : c.onClick
        return (
          <React.Fragment key={i}>
            {i > 0 && <Icon name="chevron" size={12} className="wx-crumb-sep" />}
            {go && !last
              ? <button className="wx-crumb" onClick={go}>{label}</button>
              : <span className={cx('wx-crumb', last && 'here')} aria-current={last ? 'page' : undefined}>{label}</span>}
          </React.Fragment>
        )
      })}
    </nav>
  )
}

/**
 * The page header every screen opens with.
 *   eyebrow  the module group (defaults to the shell's — "Procurement")
 *   title    the screen
 *   sub      one line on what it is for
 *   meta     a count or status beside the title
 *   actions  primary action LAST (it lands on the right edge)
 *   tabs     a Tabs row drawn under the header
 *   back     { label, onClick } — a way up from a sub-page
 */
export function PageHeader({ eyebrow, title, sub, meta, actions, tabs, back, children, className }) {
  const page = usePage()
  const eb = eyebrow === undefined ? page.group : eyebrow
  return (
    <header className={cx('wx-ph', tabs && 'has-tabs', className)}>
      <div className="wx-ph-row">
        <div className="wx-ph-text">
          {back && (
            <button className="wx-ph-back" onClick={back.onClick} title={back.title || `Back to ${back.label}`}>
              <Icon name="arrowLeft" size={14} />{back.label}</button>
          )}
          {!back && eb && <div className="wx-ph-eyebrow">{page.icon && <Icon name={page.icon} size={13} />}{eb}</div>}
          <div className="wx-ph-titlerow">
            <h1 className="wx-ph-title">{title}</h1>
            {meta != null && meta !== false && <span className="wx-ph-meta">{meta}</span>}
          </div>
          {sub && <p className="wx-ph-sub">{sub}</p>}
        </div>
        {actions && <div className="wx-ph-acts">{actions}</div>}
      </div>
      {children}
      {tabs}
    </header>
  )
}

/** Underline tabs. items: [value, label, count?, icon?] */
export function Tabs({ value, onChange, items, className, size }) {
  return (
    <div className={cx('wx-tabs', size && 'wx-tabs-' + size, className)} role="tablist">
      {items.filter(Boolean).map(([v, label, count, icon]) => (
        <button key={v} role="tab" aria-selected={value === v} className={cx('wx-tab', value === v && 'on')}
          onClick={() => onChange(v)}>
          {icon && <Icon name={icon} size={15} />}
          <span>{label}</span>
          {count != null && <span className="wx-tab-n">{count}</span>}
        </button>
      ))}
    </div>
  )
}

/**
 * The operational summary: one strip of figures divided by hairlines.
 * items: { label, value, sub, tone ('ok'|'warn'|'err'|'info'|'brand'), icon, onClick, title }
 */
export function Kpis({ items, className }) {
  const list = (items || []).filter(Boolean)
  return (
    <div className={cx('wx-kpis', className)} style={{ '--n': list.length }}>
      {list.map((k, i) => {
        const Tag = k.onClick ? 'button' : 'div'
        return (
          <Tag key={k.label || i} className={cx('wx-kpi', k.tone && 'tone-' + k.tone, k.onClick && 'act')}
            onClick={k.onClick} title={k.title}>
            <span className="wx-kpi-label">{k.icon && <Icon name={k.icon} size={14} />}{k.label}</span>
            <span className="wx-kpi-value">{k.value}</span>
            {k.sub != null && <span className="wx-kpi-sub">{k.sub}</span>}
            {k.bar != null && <span className="wx-kpi-bar"><i style={{ width: Math.max(0, Math.min(100, k.bar)) + '%' }} /></span>}
          </Tag>
        )
      })}
    </div>
  )
}

/**
 * The data workspace: a head (title, count, actions), an optional toolbar row,
 * the content (usually a table), and a footer (the pager).
 */
export function DataPanel({ title, count, sub, actions, toolbar, footer, children, className, flush, id }) {
  return (
    <section className={cx('wx-panel', flush && 'flush', className)} id={id}>
      {(title || actions) && (
        <div className="wx-panel-head">
          <div className="wx-panel-title">
            {title && <h3>{title}</h3>}
            {count != null && <span className="wx-count">{count}</span>}
            {sub && <span className="wx-panel-sub">{sub}</span>}
          </div>
          {actions && <div className="wx-panel-acts">{actions}</div>}
        </div>
      )}
      {toolbar && <div className="wx-toolbar">{toolbar}</div>}
      <div className="wx-panel-body">{children}</div>
      {footer && <div className="wx-panel-foot">{footer}</div>}
    </section>
  )
}

/** A toolbar row: search and primary filters on the left, the rest pushed right. */
export function Toolbar({ children, end, className }) {
  return (
    <div className={cx('wx-toolbar', className)}>
      {children}
      {end && <div className="wx-toolbar-end">{end}</div>}
    </div>
  )
}

/** A status pill. tone: ok | warn | err | info | brand | muted */
export function StatusBadge({ tone = 'muted', children, dot = true, title }) {
  return <span className={'wx-badge tone-' + tone} title={title}>{dot && <i />}{children}</span>
}

// ---- one state system ---------------------------------------------------
export function EmptyState({ icon = 'inbox', title, children, action, compact, className }) {
  return (
    <div className={cx('wx-state', 'empty', compact && 'compact', className)} role="status">
      <span className="wx-state-ico"><Icon name={icon} size={compact ? 18 : 22} /></span>
      {title && <b className="wx-state-title">{title}</b>}
      {children && <div className="wx-state-text">{children}</div>}
      {action && <div className="wx-state-act">{action}</div>}
    </div>
  )
}

export function LoadingState({ label = 'Loading…', rows = 5, compact, className }) {
  return (
    <div className={cx('wx-state', 'loading', compact && 'compact', className)} role="status" aria-live="polite">
      <div className="wx-skel" aria-hidden="true">
        {Array.from({ length: rows }, (_, i) => <i key={i} style={{ width: (92 - (i % 3) * 14) + '%' }} />)}
      </div>
      <span className="wx-state-text">{label}</span>
    </div>
  )
}

export function ErrorState({ title = 'Something went wrong', children, onRetry, className }) {
  return (
    <div className={cx('wx-state', 'error', className)} role="alert">
      <span className="wx-state-ico"><Icon name="alert" size={22} /></span>
      <b className="wx-state-title">{title}</b>
      {children && <div className="wx-state-text">{children}</div>}
      {onRetry && <div className="wx-state-act"><button className="btn" onClick={onRetry}><Icon name="refresh" size={14} />Try again</button></div>}
    </div>
  )
}

/** A panel from the right. Esc and the scrim close it. */
export function Drawer({ open = true, onClose, title, sub, children, footer, width = 520, className }) {
  useEffect(() => {
    if (!open) return
    const esc = (e) => { if (e.key === 'Escape') onClose?.() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="wx-drawer-wrap">
      <div className="wx-drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside className={cx('wx-drawer', className)} role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined}
        style={{ '--w': typeof width === 'number' ? width + 'px' : width }}>
        <header className="wx-drawer-head">
          <div className="wx-drawer-titles">
            <b>{title}</b>
            {sub && <small>{sub}</small>}
          </div>
          <button className="wx-iconbtn" onClick={onClose} aria-label="Close"><Icon name="x" size={16} /></button>
        </header>
        <div className="wx-drawer-body">{children}</div>
        {footer && <footer className="wx-drawer-foot">{footer}</footer>}
      </aside>
    </div>
  )
}

/** A titled group of fields inside a form. */
export function FormSection({ icon, title, note, children, cols = 2, className, aside }) {
  return (
    <section className={cx('wx-fsec', className)}>
      <header className="wx-fsec-head">
        {icon && <span className="wx-fsec-ico"><Icon name={icon} size={15} /></span>}
        <div><b>{title}</b>{note && <small>{note}</small>}</div>
        {aside && <div className="wx-fsec-aside">{aside}</div>}
      </header>
      <div className="wx-fsec-grid" style={{ '--cols': cols }}>{children}</div>
    </section>
  )
}

/** The sticky action area at the foot of a form or a workspace. */
export function ActionBar({ summary, children, className }) {
  return (
    <div className={cx('wx-actionbar', className)}>
      {summary && <div className="wx-actionbar-sum">{summary}</div>}
      <div className="wx-actionbar-acts">{children}</div>
    </div>
  )
}

/** A labelled figure inside a summary, a detail head or a footer. */
export function Stat({ label, value, tone, sub }) {
  return (
    <div className={cx('wx-stat', tone && 'tone-' + tone)}>
      <span className="wx-stat-label">{label}</span>
      <b className="wx-stat-value">{value}</b>
      {sub && <small>{sub}</small>}
    </div>
  )
}

/** The record header on a detail view: identity, status, key facts, actions. */
export function RecordHead({ kicker, title, status, facts, actions, onBack, backLabel = 'Back' }) {
  return (
    <div className="wx-record">
      {onBack && <button className="wx-ph-back" onClick={onBack}><Icon name="arrowLeft" size={14} />{backLabel}</button>}
      <div className="wx-record-row">
        <div className="wx-record-id">
          {kicker && <span className="wx-record-kicker">{kicker}</span>}
          <div className="wx-record-title"><h2>{title}</h2>{status}</div>
        </div>
        {actions && <div className="wx-ph-acts">{actions}</div>}
      </div>
      {facts && facts.length > 0 && (
        <dl className="wx-record-facts">
          {facts.filter(Boolean).map(([k, v]) => (
            <div key={k}><dt>{k}</dt><dd>{v ?? '—'}</dd></div>
          ))}
        </dl>
      )}
    </div>
  )
}
