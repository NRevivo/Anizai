/*
 * Calibration curve — predicted probability against actual outcome rate.
 *
 * Form: a scatter against a 45° reference line. Not a bar chart — the whole
 * question is "how far from the diagonal", and only a plot with matched x and
 * y scales can show that distance.
 *
 * Both axes are 0–1 and share a scale, so the diagonal is a real 45°. A
 * mismatched pair of ranges would make a well-calibrated agent look skewed.
 *
 * The reference line is the *only* second mark, so it takes slot 2 (orange)
 * against the agent's slot 1 (blue) — warm against cool, and the legend names
 * both. Two series means a legend is required.
 *
 * Every point is table-backed below the plot: aqua/blue at these sizes sits
 * near the contrast floor on the light surface, and the table is the relief.
 * It is also the WCAG-clean twin — no value here is reachable only by hover.
 */

import {
  CartesianGrid,
  ErrorBar,
  Legend,
  Line,
  ComposedChart,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { CalibrationCurve } from '../lib/api'
import { EmptyState, SampleSize } from '../components/primitives'

const DIAGONAL = [
  { x: 0, y: 0 },
  { x: 1, y: 1 },
]

export function CalibrationCurveChart({ data }: { data: CalibrationCurve }) {
  const populated = data.points.filter((p) => p.count > 0)

  if (populated.length === 0) {
    return (
      <EmptyState title="No scored forecasts yet">
        The curve appears once questions start resolving. A forecast counts
        when it completed, carried a probability, and its market settled to
        YES or NO.
      </EmptyState>
    )
  }

  const points = populated.map((p) => ({
    x: p.mean_predicted!,
    y: p.actual_yes_rate!,
    bucket: p.bucket,
    count: p.count,
    // Recharts ErrorBar wants offsets from the point, not absolute bounds.
    errorY: [
      p.actual_yes_rate! - p.lower_bound,
      p.upper_bound - p.actual_yes_rate!,
    ] as [number, number],
  }))

  return (
    <>
      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart margin={{ top: 8, right: 16, bottom: 28, left: 8 }}>
            <CartesianGrid stroke="var(--gridline)" strokeWidth={1} />
            <XAxis
              type="number"
              dataKey="x"
              domain={[0, 1]}
              ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]}
              tickFormatter={(v) => `${Math.round(v * 100)}%`}
              stroke="var(--baseline)"
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              label={{
                value: 'agent said',
                position: 'insideBottom',
                offset: -14,
                fill: 'var(--text-secondary)',
                fontSize: 12,
              }}
            />
            <YAxis
              type="number"
              dataKey="y"
              domain={[0, 1]}
              ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]}
              tickFormatter={(v) => `${Math.round(v * 100)}%`}
              stroke="var(--baseline)"
              tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
              label={{
                value: 'actually happened',
                angle: -90,
                position: 'insideLeft',
                fill: 'var(--text-secondary)',
                fontSize: 12,
              }}
            />
            <Tooltip
              cursor={{ stroke: 'var(--baseline)' }}
              contentStyle={{
                background: 'var(--surface-1)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                fontSize: 12,
                color: 'var(--text-primary)',
              }}
              formatter={(value: number, name: string) => [
                `${Math.round(value * 100)}%`,
                name,
              ]}
            />
            <Legend
              verticalAlign="top"
              height={28}
              wrapperStyle={{ fontSize: 12, color: 'var(--text-secondary)' }}
            />
            <Line
              name="perfect calibration"
              data={DIAGONAL}
              dataKey="y"
              stroke="var(--series-2)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              legendType="line"
            />
            <Scatter
              name="the agent"
              data={points}
              fill="var(--series-1)"
              isAnimationActive={false}
              legendType="circle"
            >
              <ErrorBar
                dataKey="errorY"
                stroke="var(--series-1)"
                strokeWidth={2}
                width={6}
                direction="y"
              />
            </Scatter>
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="table-scroll" style={{ marginTop: 12 }}>
        <table>
          <thead>
            <tr>
              <th>bucket</th>
              <th className="num">n</th>
              <th className="num">agent said</th>
              <th className="num">actually happened</th>
              <th className="num">95% interval</th>
            </tr>
          </thead>
          <tbody>
            {data.points.map((p) => (
              <tr key={p.bucket}>
                <td>{p.bucket}</td>
                <td className="num">{p.count}</td>
                <td className="num">
                  {p.mean_predicted === null
                    ? '—'
                    : `${(p.mean_predicted * 100).toFixed(0)}%`}
                </td>
                <td className="num">
                  {p.actual_yes_rate === null
                    ? '—'
                    : `${(p.actual_yes_rate * 100).toFixed(0)}%`}
                </td>
                <td className="num">
                  {p.count === 0
                    ? '—'
                    : `${(p.lower_bound * 100).toFixed(0)}–${(
                        p.upper_bound * 100
                      ).toFixed(0)}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p style={{ marginTop: 10, marginBottom: 0 }}>
        <SampleSize n={data.total_forecasts} />
      </p>
    </>
  )
}
