/*
 * Formatting helpers.
 *
 * One rule runs through all of these: **an absent number renders as an em
 * dash, never as zero.** In this dashboard 0.0 is a perfect Brier score, so
 * a null rendered as 0 would show a flawless forecast where there is in fact
 * no data at all — the single most misleading thing this UI could do.
 */

export const EM_DASH = '—'

export function probability(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH
  return `${(value * 100).toFixed(0)}%`
}

export function brier(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH
  return value.toFixed(4)
}

export function delta(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH
  return `${value >= 0 ? '+' : ''}${value.toFixed(4)}`
}

export function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH
  return String(value)
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH
  return `$${Math.round(value).toLocaleString('en-US')}`
}

export function date(iso: string | null | undefined): string {
  if (!iso) return EM_DASH
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return EM_DASH
  return parsed.toISOString().slice(0, 10)
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return EM_DASH
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return EM_DASH
  return parsed.toISOString().slice(0, 16).replace('T', ' ')
}

/*
 * Brier quality band.
 *
 * Thresholds are anchored on the uninformed baseline of 0.25 — the score you
 * get by always saying 50%. Anything at or above it means the forecast added
 * nothing, which is why 0.25 is the boundary of "critical" rather than a
 * midpoint of some arbitrary scale.
 */
export function brierBand(
  value: number | null | undefined,
): 'good' | 'warning' | 'critical' | 'none' {
  if (value === null || value === undefined) return 'none'
  if (value <= 0.1) return 'good'
  if (value < 0.25) return 'warning'
  return 'critical'
}

/*
 * A forecast status rendered for a human.
 *
 * `needs_clarification` reads as "asked for clarification" rather than
 * anything failure-shaped: the agent did its job and asked a reasonable
 * question, and labelling it as a failure would misreport the agent's
 * behaviour to whoever is reading the number.
 */
export const STATUS_LABEL: Record<string, string> = {
  dispatched: 'in flight',
  completed: 'completed',
  failed: 'failed',
  timed_out: 'timed out',
  needs_clarification: 'asked for clarification',
}

export function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status
}
