/*
 * The six screens.
 *
 * Overview answers "is anything stuck", Questions and QuestionDetail answer
 * "what happened to this market", Metrics answers "is the agent any good",
 * Runs answers "did the machinery fire", and ManualAdd is the one write.
 *
 * A shared `useAsync` holds the previous render while refetching rather than
 * flashing a skeleton — a layout jump on every poll is worse than a stale
 * number for half a second.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  api,
  ApiError,
  type CalibrationCurve,
  type CohortBrier,
  type ImprovementCurve,
  type Overview,
  type QuestionDetail,
  type QuestionRow,
  type RunRow,
  type SourceContribution,
} from '../lib/api'
import {
  brier,
  count,
  date,
  dateTime,
  probability,
  statusLabel,
  usd,
} from '../lib/format'
import {
  BrierBadge,
  Card,
  Caveat,
  EmptyState,
  ErrorNote,
  Loading,
  SampleSize,
  StatTile,
} from './primitives'
import { CalibrationCurveChart } from '../charts/CalibrationCurveChart'
import { CohortBrierChart } from '../charts/CohortBrierChart'
import { ImprovementCurveChart } from '../charts/ImprovementCurveChart'
import { SourceContributionTable } from '../charts/SourceContributionTable'

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const run = useCallback(() => {
    let cancelled = false
    setLoading(true)
    loader()
      .then((result) => {
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(
          err instanceof ApiError
            ? err.isNotOperator
              ? 'This account is not on the operator allowlist.'
              : err.message
            : String(err),
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(run, [run])
  return { data, error, loading, reload: run }
}

/* ==========================================================
 * 1. Overview
 * ========================================================== */

export function OverviewScreen() {
  const { data, error, loading } = useAsync<Overview>(() => api.overview())

  if (error) return <ErrorNote message={error} />
  if (loading && !data) return <Loading what="overview" />
  if (!data) return null

  const stuck =
    data.failedForecasts + data.timedOutForecasts + data.needsClarification

  return (
    <>
      <div className="tiles">
        <StatTile label="Open questions" value={data.openQuestions} />
        <StatTile label="Resolved" value={data.resolvedQuestions} />
        <StatTile
          label="Scored forecasts"
          value={data.scorableForecasts}
          note="completed, on a YES/NO market"
        />
        <StatTile
          label="Mean Brier"
          value={brier(data.latestAggregateBrier)}
          note={`coin flip = ${data.uninformedBaseline.toFixed(2)}`}
        />
      </div>

      <Card
        title="Forecast states"
        subtitle="Failures sit beside successes on purpose — the first question is whether anything is stuck."
      >
        <div className="tiles" style={{ marginBottom: 0 }}>
          <StatTile label="In flight" value={data.dispatchedForecasts} />
          <StatTile label="Completed" value={data.completedForecasts} />
          <StatTile
            label="Failed"
            value={data.failedForecasts}
            attention={data.failedForecasts > 0}
          />
          <StatTile
            label="Timed out"
            value={data.timedOutForecasts}
            attention={data.timedOutForecasts > 0}
          />
          <StatTile
            label="Asked for clarification"
            value={data.needsClarification}
            note="not a failure"
          />
        </div>
        {stuck === 0 && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 0 }}>
            Nothing failed, timed out, or needs clarification.
          </p>
        )}
      </Card>

      <Card title="Question pool" subtitle="Open questions against each cohort's target.">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>cohort</th>
                <th className="num">open</th>
                <th className="num">target</th>
                <th>status</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.cohortTargets).map(([cohort, target]) => {
                const open = data.openByCohort[cohort] ?? 0
                return (
                  <tr key={cohort}>
                    <td>{cohort}</td>
                    <td className="num">{open}</td>
                    <td className="num">{target}</td>
                    <td>
                      {open >= target ? (
                        'at target'
                      ) : (
                        <span style={{ color: 'var(--status-warning)' }}>
                          short by {target - open}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Provenance">
        <div className="table-scroll">
          <table>
            <tbody>
              <tr>
                <th>Latest agent version</th>
                <td>{data.latestAgentVersion ?? '—'}</td>
              </tr>
              <tr>
                <th>Latest metrics snapshot</th>
                <td>{dateTime(data.latestSnapshotAt)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>
    </>
  )
}

/* ==========================================================
 * 2. Questions
 * ========================================================== */

export function QuestionsScreen({
  onOpen,
}: {
  onOpen: (id: string) => void
}) {
  const [status, setStatus] = useState('')
  const [cohort, setCohort] = useState('')
  const [category, setCategory] = useState('')

  const { data, error, loading } = useAsync(
    () => api.questions({ status, cohort, category }),
    [status, cohort, category],
  )

  return (
    <>
      {/* One filter row above everything it scopes — never inside a card. */}
      <div className="filters">
        <label htmlFor="f-status">status</label>
        <select
          id="f-status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">all</option>
          <option value="open">open</option>
          <option value="resolved">resolved</option>
          <option value="archived">archived</option>
        </select>

        <label htmlFor="f-cohort">cohort</label>
        <select
          id="f-cohort"
          value={cohort}
          onChange={(e) => setCohort(e.target.value)}
        >
          <option value="">all</option>
          <option value="7d">7d</option>
          <option value="14d">14d</option>
          <option value="30-45d">30-45d</option>
        </select>

        <label htmlFor="f-category">category</label>
        <select
          id="f-category"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="">all</option>
          <option value="geopolitical">geopolitical</option>
          <option value="financial">financial</option>
          <option value="ai">ai</option>
          <option value="other">other</option>
        </select>
      </div>

      <Card>
        {error && <ErrorNote message={error} />}
        {loading && !data && <Loading what="questions" />}
        {data && data.items.length === 0 && (
          <EmptyState title="No questions match">
            Run discovery to populate the pool, or clear the filters.
          </EmptyState>
        )}
        {data && data.items.length > 0 && (
          <div
            className="table-scroll"
            style={{ opacity: loading ? 0.6 : 1 }}
          >
            <table>
              <thead>
                <tr>
                  <th>question</th>
                  <th>cohort</th>
                  <th>category</th>
                  <th>status</th>
                  <th>resolves</th>
                  <th className="num">forecasts</th>
                  <th className="num">latest</th>
                  <th>Brier</th>
                  <th>outcome</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((q: QuestionRow) => (
                  <tr
                    key={q.id}
                    className="clickable"
                    onClick={() => onOpen(q.id)}
                  >
                    <td style={{ maxWidth: 320 }}>{q.questionText}</td>
                    <td>{q.cohort}</td>
                    <td>{q.category}</td>
                    <td>{q.status}</td>
                    <td>{date(q.expectedResolutionDate)}</td>
                    <td className="num">{q.forecastCount}</td>
                    <td className="num">{probability(q.latestProbability)}</td>
                    <td>
                      <BrierBadge value={q.latestBrier} />
                    </td>
                    <td>{q.outcome ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

/* ==========================================================
 * 3. Question detail
 * ========================================================== */

export function QuestionDetailScreen({
  questionId,
  onBack,
}: {
  questionId: string
  onBack: () => void
}) {
  const { data, error, loading } = useAsync<QuestionDetail>(
    () => api.question(questionId),
    [questionId],
  )

  if (error) return <ErrorNote message={error} />
  if (loading && !data) return <Loading what="question" />
  if (!data) return null

  const { question, forecasts, resolution } = data

  return (
    <>
      <button className="action" onClick={onBack} style={{ marginBottom: 16 }}>
        ← All questions
      </button>

      <Card title={question.questionText}>
        <div className="table-scroll">
          <table>
            <tbody>
              <tr>
                <th>cohort / category</th>
                <td>
                  {question.cohort} · {question.category}
                </td>
              </tr>
              <tr>
                <th>status</th>
                <td>{question.status}</td>
              </tr>
              <tr>
                <th>expected resolution</th>
                <td>{date(question.expectedResolutionDate)}</td>
              </tr>
              <tr>
                <th>liquidity at pickup</th>
                <td>{usd(question.liquidityAtPickup)}</td>
              </tr>
              <tr>
                <th>source</th>
                <td>
                  {question.addedBy}
                  {question.addedByOperator ? ` · ${question.addedByOperator}` : ''}
                </td>
              </tr>
              <tr>
                <th>market</th>
                <td>
                  <a
                    href={question.polymarketUrl}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {question.polymarketUrl}
                  </a>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>

      <Card
        title="Resolution"
        subtitle={
          resolution
            ? undefined
            : 'Not resolved yet — the resolver polls Polymarket hourly.'
        }
      >
        {resolution ? (
          <div className="table-scroll">
            <table>
              <tbody>
                <tr>
                  <th>outcome</th>
                  <td>
                    {resolution.outcome}
                    {!resolution.scorable && (
                      <span style={{ color: 'var(--text-muted)' }}>
                        {' '}
                        · excluded from every metric
                      </span>
                    )}
                  </td>
                </tr>
                <tr>
                  <th>resolved at</th>
                  <td>{dateTime(resolution.resolvedAt)}</td>
                </tr>
                <tr>
                  <th>detected at</th>
                  <td>{dateTime(resolution.detectedAt)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="Awaiting resolution" />
        )}
      </Card>

      <Card
        title="Forecast history"
        subtitle="Run 0 is the original forecast; the highest run index is the latest."
      >
        {forecasts.length === 0 ? (
          <EmptyState title="No forecast has been dispatched for this question" />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th className="num">run</th>
                  <th>status</th>
                  <th className="num">probability</th>
                  <th className="num">confidence</th>
                  <th>tier</th>
                  <th>Brier</th>
                  <th className="num">evidence</th>
                  <th>agent version</th>
                  <th>completed</th>
                </tr>
              </thead>
              <tbody>
                {forecasts.map((f) => (
                  <tr key={f.id}>
                    <td className="num">{f.runIndex}</td>
                    <td>
                      {statusLabel(f.status)}
                      {f.errorMessage && (
                        <div
                          style={{ color: 'var(--text-muted)', fontSize: 12 }}
                        >
                          {f.errorMessage}
                        </div>
                      )}
                    </td>
                    <td className="num">{probability(f.probability)}</td>
                    <td className="num">{probability(f.confidence)}</td>
                    <td>{f.tier ?? '—'}</td>
                    <td>
                      <BrierBadge value={f.brierScore} />
                    </td>
                    <td className="num">
                      {count(
                        (f.evidenceSummary?.evidence_count_total as number) ??
                          null,
                      )}
                    </td>
                    <td>{f.agentVersion ?? '—'}</td>
                    <td>{dateTime(f.completedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

/* ==========================================================
 * 4. Metrics
 * ========================================================== */

export function MetricsScreen() {
  const curve = useAsync<CalibrationCurve>(() => api.calibrationCurve())
  const cohorts = useAsync<CohortBrier>(() => api.cohortBrier())
  const improvement = useAsync<ImprovementCurve>(() => api.improvementCurve())
  const sources = useAsync<SourceContribution>(() => api.sourceContribution())

  return (
    <>
      <Card
        title="Calibration curve"
        subtitle="When the agent says 70%, does it happen 70% of the time? Perfect calibration is the diagonal."
      >
        {curve.error && <ErrorNote message={curve.error} />}
        {curve.loading && !curve.data && <Loading what="the curve" />}
        {curve.data && (
          <>
            <CalibrationCurveChart data={curve.data} />
            {curve.data.aggregate_brier !== null && (
              <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                Aggregate Brier {brier(curve.data.aggregate_brier)} · skill vs
                a coin flip{' '}
                {curve.data.skill_vs_coin_flip !== null
                  ? `${curve.data.skill_vs_coin_flip >= 0 ? '+' : ''}${curve.data.skill_vs_coin_flip.toFixed(3)}`
                  : '—'}
              </p>
            )}
          </>
        )}
      </Card>

      <Card
        title="Brier by horizon"
        subtitle="Accuracy usually decays as the horizon lengthens. Lower is better."
      >
        {cohorts.error && <ErrorNote message={cohorts.error} />}
        {cohorts.loading && !cohorts.data && <Loading what="cohort scores" />}
        {cohorts.data && <CohortBrierChart data={cohorts.data} />}
      </Card>

      <Card
        title="Did re-forecasting help?"
        subtitle="Each bar is one resolved question: its first forecast against its last."
      >
        {improvement.error && <ErrorNote message={improvement.error} />}
        {improvement.loading && !improvement.data && (
          <Loading what="the improvement series" />
        )}
        {improvement.data && <ImprovementCurveChart data={improvement.data} />}
      </Card>

      <Card
        title="Vault contribution"
        subtitle="Forecasts a vault contributed to, against the ones it did not."
      >
        {sources.error && <ErrorNote message={sources.error} />}
        {sources.loading && !sources.data && <Loading what="vault attribution" />}
        {sources.data && <SourceContributionTable data={sources.data} />}
      </Card>
    </>
  )
}

/* ==========================================================
 * 5. Runs
 * ========================================================== */

export function RunsScreen() {
  const { data, error, loading, reload } = useAsync(() => api.runs())
  const [triggering, setTriggering] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  async function trigger() {
    setTriggering(true)
    setNote(null)
    try {
      const result = await api.triggerRun()
      setNote(
        `Discovery finished — ${result.inserted ?? 0} question(s) added, ` +
          `${result.candidatesFound ?? 0} candidate(s) considered.`,
      )
      reload()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : String(err))
    } finally {
      setTriggering(false)
    }
  }

  return (
    <>
      <Card
        title="Trigger discovery"
        subtitle="Tops the question pool up to its cohort targets."
      >
        <button className="action" onClick={trigger} disabled={triggering}>
          {triggering ? 'Running…' : 'Run discovery now'}
        </button>
        {/* Stated in the UI, not only in the API: this button cannot spend
            tokens, and an operator should be able to tell that before
            clicking it. */}
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 0 }}>
          Discovery only. Dispatching forecasts costs tokens and is not
          available from this dashboard.
        </p>
        {note && <p style={{ fontSize: 13 }}>{note}</p>}
      </Card>

      <Card title="Run history">
        {error && <ErrorNote message={error} />}
        {loading && !data && <Loading what="runs" />}
        {data && data.items.length === 0 && (
          <EmptyState title="Nothing has run yet">
            Discovery, dispatch, harvest, and resolve each record a run.
          </EmptyState>
        )}
        {data && data.items.length > 0 && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>started</th>
                  <th>type</th>
                  <th>by</th>
                  <th className="num">dispatched</th>
                  <th className="num">completed</th>
                  <th className="num">failed</th>
                  <th>finished</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((run: RunRow) => (
                  <tr key={run.id}>
                    <td>{dateTime(run.triggeredAt)}</td>
                    <td>{run.runType}</td>
                    <td>{run.triggeredBy}</td>
                    <td className="num">{count(run.questionsDispatched)}</td>
                    <td className="num">{count(run.forecastsCompleted)}</td>
                    <td className="num">{count(run.forecastsFailed)}</td>
                    <td>
                      {run.isFinished ? (
                        dateTime(run.finishedAt)
                      ) : (
                        <span style={{ color: 'var(--status-warning)' }}>
                          still running
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

/* ==========================================================
 * 6. Manual add
 * ========================================================== */

export function ManualAddScreen() {
  const [slug, setSlug] = useState('')
  const [category, setCategory] = useState('geopolitical')
  const [cohort, setCohort] = useState('7d')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setResult(null)
    setFailure(null)
    try {
      const response = await api.addQuestion({
        polymarket_slug: slug.trim(),
        category,
        cohort,
      })
      setResult(
        response.alreadyTracked
          ? 'That market is already tracked — nothing was added.'
          : 'Added.',
      )
      setSlug('')
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      title="Add a question by hand"
      subtitle="Everything except category and cohort is looked up from Polymarket."
    >
      <form onSubmit={submit}>
        <div className="filters">
          <label htmlFor="slug">market slug</label>
          <input
            id="slug"
            type="text"
            value={slug}
            placeholder="us-govt-shutdown-august"
            onChange={(e) => setSlug(e.target.value)}
            style={{ minWidth: 280 }}
            required
          />

          <label htmlFor="cat">category</label>
          <select id="cat" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="geopolitical">geopolitical</option>
            <option value="financial">financial</option>
            <option value="ai">ai</option>
            <option value="other">other</option>
          </select>

          <label htmlFor="coh">cohort</label>
          <select id="coh" value={cohort} onChange={(e) => setCohort(e.target.value)}>
            <option value="7d">7d</option>
            <option value="14d">14d</option>
            <option value="30-45d">30-45d</option>
          </select>

          <button className="action" type="submit" disabled={busy || !slug.trim()}>
            {busy ? 'Adding…' : 'Add'}
          </button>
        </div>
      </form>

      {result && <p style={{ fontSize: 13 }}>{result}</p>}
      {failure && <ErrorNote message={failure} />}

      <Caveat>
        The slug is the last path segment of the market URL —
        polymarket.com/event/<strong>this-part</strong>. The condition id and
        resolution date are read from Polymarket, so a wrong slug fails loudly
        rather than silently attaching this question to another market.
      </Caveat>
    </Card>
  )
}

export { SampleSize }
