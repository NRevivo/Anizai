/*
 * Did re-forecasting closer to the event help?
 *
 * The value is a signed delta, so the encoding is **diverging**: blue for
 * improved, red for worsened, around a zero baseline. Warm against cool, with
 * the neutral at zero, so the sign is readable without consulting a legend
 * key. This is one of the few places a value legitimately determines colour —
 * because the value *is* a polarity, not a magnitude.
 *
 * Sign is never carried by colour alone: bars sit above or below the zero
 * line, and the table spells out both scores.
 *
 * When there are too few paired questions the chart renders behind an
 * explicit caveat rather than being hidden. Hiding it would leave the
 * operator unable to see the data at all; rendering it bare would invite
 * reading three unlucky questions as a regression.
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
import type { ImprovementCurve } from '../lib/api'
import { Caveat, EmptyState } from '../components/primitives'
import { brier, delta, probability } from '../lib/format'

export function ImprovementCurveChart({ data }: { data: ImprovementCurve }) {
  if (data.n_paired_questions === 0) {
    return (
      <EmptyState title="No question has been forecast twice and resolved yet">
        {data.single_forecast_questions > 0
          ? `${data.single_forecast_questions} question(s) have a single forecast — there is no before-and-after to compare.`
          : 'This fills in after the first weekly re-forecast cycle resolves.'}
      </EmptyState>
    )
  }

  const rows = data.points.map((p, index) => ({
    label: `#${index + 1}`,
    delta: p.delta,
    improved: p.improved,
    cohort: p.cohort,
    original: p.original_brier,
    latest: p.latest_brier,
    originalProbability: p.original_probability,
    latestProbability: p.latest_probability,
  }))

  return (
    <>
      {!data.interpretable && (
        <Caveat>
          {data.n_paired_questions} paired question
          {data.n_paired_questions === 1 ? '' : 's'} — below the{' '}
          {data.min_interpretable_n} needed for this delta to mean anything. At
          this sample size the sign is noise, not a trend.
        </Caveat>
      )}

      <div className="chart-wrap" style={{ marginTop: 12 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 28, left: 8 }}>
            <CartesianGrid stroke="var(--gridline)" vertical={false} />
            <XAxis
              dataKey="label"
              stroke="var(--baseline)"
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              label={{
                value: 'resolved questions, in order',
                position: 'insideBottom',
                offset: -14,
                fill: 'var(--text-secondary)',
                fontSize: 12,
              }}
            />
            <YAxis
              stroke="var(--baseline)"
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              label={{
                value: 'Brier improvement',
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
                `${value >= 0 ? '+' : ''}${value.toFixed(4)} — ${
                  entry?.payload?.improved ? 'improved' : 'worsened'
                }`,
                entry?.payload?.cohort ?? '',
              ]}
            />
            <ReferenceLine y={0} stroke="var(--baseline)" strokeWidth={2} />
            <Bar dataKey="delta" radius={[4, 4, 0, 0]} isAnimationActive={false}>
              {rows.map((row, index) => (
                <Cell
                  key={index}
                  fill={
                    row.improved
                      ? 'var(--diverge-positive)'
                      : 'var(--diverge-negative)'
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="table-scroll" style={{ marginTop: 12 }}>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>cohort</th>
              <th className="num">first said</th>
              <th className="num">later said</th>
              <th className="num">first Brier</th>
              <th className="num">later Brier</th>
              <th className="num">change</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                <td>{row.label}</td>
                <td>{row.cohort}</td>
                <td className="num">{probability(row.originalProbability)}</td>
                <td className="num">{probability(row.latestProbability)}</td>
                <td className="num">{brier(row.original)}</td>
                <td className="num">{brier(row.latest)}</td>
                <td
                  className="num"
                  style={{
                    color: row.improved
                      ? 'var(--success-text)'
                      : 'var(--status-critical)',
                  }}
                >
                  {delta(row.delta)} {row.improved ? 'better' : 'worse'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 0 }}>
        {data.improved_count} improved · {data.worsened_count} worsened ·{' '}
        {data.single_forecast_questions} excluded for having a single forecast
      </p>
    </>
  )
}
