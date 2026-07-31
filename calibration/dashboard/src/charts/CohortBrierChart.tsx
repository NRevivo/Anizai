/*
 * Brier score by resolution horizon.
 *
 * One series — magnitude across four named categories — so every bar takes
 * slot 1 and there is no legend. Colouring bars darker-where-bigger would
 * double-encode length as hue and burn the free channel on information the
 * bar length already carries.
 *
 * The 0.25 reference line is the score you get by always saying 50%. Without
 * it a reader has no idea whether 0.19 is good, and it is the first thing
 * anyone should ask. It is slot 2 (orange) and labelled directly, since it is
 * the only other mark.
 *
 * Lower is better here, which is unusual enough that the axis says so.
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { CohortBrier } from '../lib/api'
import { EmptyState } from '../components/primitives'
import { brier } from '../lib/format'

export function CohortBrierChart({ data }: { data: CohortBrier }) {
  const populated = data.items.filter((i) => i.n > 0)

  if (populated.length === 0) {
    return (
      <EmptyState title="No cohort has a resolved forecast yet">
        Each cohort scores separately so short-horizon and long-horizon
        accuracy stay comparable.
      </EmptyState>
    )
  }

  const rows = data.items.map((i) => ({
    cohort: i.cohort,
    brier: i.mean_brier,
    n: i.n,
    smallSample: i.small_sample,
  }))

  return (
    <>
      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 28, left: 8 }}>
            <CartesianGrid stroke="var(--gridline)" vertical={false} />
            <XAxis
              dataKey="cohort"
              stroke="var(--baseline)"
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
            />
            <YAxis
              /*
               * The domain must always contain the 0.25 baseline.
               * `[0, 'auto']` scales to the data, so when every cohort scores
               * better than a coin flip — the case we most want to see — the
               * reference line falls outside the domain and Recharts silently
               * discards it. The chart would then lose its reference point at
               * exactly the moment the reference point is good news.
               */
              domain={[0, (dataMax: number) => Math.max(0.3, dataMax * 1.15)]}
              stroke="var(--baseline)"
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              label={{
                value: 'Brier — lower is better',
                angle: -90,
                position: 'insideLeft',
                fill: 'var(--text-secondary)',
                fontSize: 12,
              }}
            />
            <Tooltip
              cursor={{ fill: 'var(--page)' }}
              contentStyle={{
                background: 'var(--surface-1)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                fontSize: 12,
                color: 'var(--text-primary)',
              }}
              formatter={(value: number, _name, entry) => [
                `${value?.toFixed(4) ?? '—'} (n=${entry?.payload?.n ?? 0})`,
                'mean Brier',
              ]}
            />
            <ReferenceLine
              y={0.25}
              stroke="var(--series-2)"
              strokeWidth={2}
              label={{
                value: 'always saying 50%',
                position: 'right',
                fill: 'var(--series-2)',
                fontSize: 11,
              }}
            />
            <Bar dataKey="brier" radius={[4, 4, 0, 0]} isAnimationActive={false}>
              {rows.map((row) => (
                <Cell key={row.cohort} fill="var(--series-1)" />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="table-scroll" style={{ marginTop: 12 }}>
        <table>
          <thead>
            <tr>
              <th>cohort</th>
              <th className="num">n</th>
              <th className="num">mean Brier</th>
              <th className="num">std</th>
              <th className="num">vs coin flip</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <tr key={item.cohort}>
                <td>
                  {item.cohort}
                  {item.small_sample && (
                    <span style={{ color: 'var(--text-muted)' }}> · small n</span>
                  )}
                </td>
                <td className="num">{item.n}</td>
                <td className="num">{brier(item.mean_brier)}</td>
                <td className="num">{brier(item.std_brier)}</td>
                <td className="num">
                  {item.skill_vs_coin_flip === null
                    ? '—'
                    : `${item.skill_vs_coin_flip >= 0 ? '+' : ''}${item.skill_vs_coin_flip.toFixed(3)}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
