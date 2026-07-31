/*
 * Small shared pieces.
 *
 * Two carry design decisions worth stating:
 *
 * `EmptyState` is a first-class component, not an afterthought. Every screen
 * in this dashboard renders empty on day one and stays that way until the
 * first question resolves — which for the 30-45d cohort is over a month. An
 * empty screen that explains itself is the *normal* state here, not an edge
 * case.
 *
 * `SampleSize` exists so no aggregate is ever rendered without the n it was
 * computed from. A mean over three questions and a mean over forty look
 * identical and mean entirely different things.
 */

import type { ReactNode } from 'react'
import { brierBand } from '../lib/format'

export function Card({
  title,
  subtitle,
  children,
}: {
  title?: string
  subtitle?: string
  children: ReactNode
}) {
  return (
    <section className="card">
      {title && <h2>{title}</h2>}
      {subtitle && <p className="sub">{subtitle}</p>}
      {children}
    </section>
  )
}

export function EmptyState({
  title,
  children,
}: {
  title: string
  children?: ReactNode
}) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children}
    </div>
  )
}

export function Loading({ what }: { what: string }) {
  return <div className="empty">Loading {what}…</div>
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="error" role="alert">
      {message}
    </div>
  )
}

export function StatTile({
  label,
  value,
  note,
  attention = false,
}: {
  label: string
  value: ReactNode
  note?: string
  attention?: boolean
}) {
  return (
    <div className={`tile${attention ? ' attention' : ''}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
  )
}

/*
 * A Brier score with its quality band.
 *
 * The band is carried by a text label as well as a color dot — never by
 * color alone, so it survives colorblindness, print, and forced-colors.
 */
export function BrierBadge({ value }: { value: number | null | undefined }) {
  const band = brierBand(value)
  if (band === 'none') return <span>—</span>

  const color = {
    good: 'var(--status-good)',
    warning: 'var(--status-warning)',
    critical: 'var(--status-critical)',
  }[band]

  const word = { good: 'good', warning: 'fair', critical: 'poor' }[band]

  return (
    <span className="badge" title={`Brier ${value!.toFixed(4)} — ${word}`}>
      <span
        aria-hidden="true"
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          background: color,
          display: 'inline-block',
        }}
      />
      {value!.toFixed(3)} <span style={{ color: 'var(--text-muted)' }}>{word}</span>
    </span>
  )
}

/* No aggregate is rendered without the sample it came from. */
export function SampleSize({ n, threshold = 10 }: { n: number; threshold?: number }) {
  return (
    <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
      n={n}
      {n > 0 && n < threshold ? ' — too few to read as a trend' : ''}
    </span>
  )
}

export function Caveat({ children }: { children: ReactNode }) {
  return <p className="caveat">{children}</p>
}
