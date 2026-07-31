/*
 * Which vault is actually predictive?
 *
 * A table, deliberately — not a chart. Five rows of a signed delta, each of
 * which is only meaningful alongside two group sizes and a comparability
 * flag. A bar chart would show the deltas at full visual weight and hide the
 * four numbers that say whether any of them can be trusted.
 *
 * The observational-not-causal caveat is rendered every time, from the field
 * the API returns rather than as UI copy. The number will outlive anyone's
 * memory of the caveat, so the caveat travels with the data.
 */

import type { SourceContribution } from '../lib/api'
import { Caveat, EmptyState } from '../components/primitives'
import { brier, delta } from '../lib/format'

export function SourceContributionTable({ data }: { data: SourceContribution }) {
  if (data.total_forecasts === 0) {
    return (
      <EmptyState title="No scored forecasts to attribute yet">
        Once questions resolve, each vault is compared on the forecasts it
        contributed to against the ones it did not.
      </EmptyState>
    )
  }

  return (
    <>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>vault</th>
              <th className="num">used in</th>
              <th className="num">absent from</th>
              <th className="num">Brier with</th>
              <th className="num">Brier without</th>
              <th className="num">difference</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => {
              const readable = item.comparable && item.delta !== null
              return (
                <tr key={item.vault_type}>
                  <td>
                    {item.vault_type}
                    {!item.comparable && (
                      <span style={{ color: 'var(--text-muted)' }}>
                        {' '}
                        · groups under {data.min_group_size}
                      </span>
                    )}
                  </td>
                  <td className="num">{item.n_with}</td>
                  <td className="num">{item.n_without}</td>
                  <td className="num">{brier(item.mean_brier_with)}</td>
                  <td className="num">{brier(item.mean_brier_without)}</td>
                  <td
                    className="num"
                    style={{
                      color: readable
                        ? item.helps
                          ? 'var(--success-text)'
                          : 'var(--status-critical)'
                        : 'var(--text-muted)',
                    }}
                  >
                    {delta(item.delta)}
                    {readable && (item.helps ? ' helps' : ' hurts')}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 10 }}>
        A negative difference means forecasts using that vault scored better.
      </p>

      <Caveat>{data.interpretation}</Caveat>
    </>
  )
}
