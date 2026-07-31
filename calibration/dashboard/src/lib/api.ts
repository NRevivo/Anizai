/*
 * Typed API client.
 *
 * Every call attaches the Firebase ID token. The token is fetched fresh per
 * request rather than cached: Firebase rotates it hourly, and a cached token
 * produces a 401 that looks like a permissions problem rather than an expiry.
 *
 * 401 and 403 are surfaced as distinct errors on purpose — 401 means sign in
 * again, 403 means signing in again will not help. Collapsing them into
 * "unauthorized" would send an operator round a loop that cannot succeed.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  get isAuthExpired() {
    return this.status === 401
  }

  get isNotOperator() {
    return this.status === 403
  }
}

export type TokenProvider = () => Promise<string | null>

let tokenProvider: TokenProvider = async () => null

export function setTokenProvider(provider: TokenProvider) {
  tokenProvider = provider
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await tokenProvider()
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string> | undefined),
  }
  if (token) headers.Authorization = `Bearer ${token}`
  if (init.body) headers['Content-Type'] = 'application/json'

  const response = await fetch(path, { ...init, headers })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* a non-JSON error body is not itself an error worth reporting */
    }
    throw new ApiError(response.status, detail)
  }

  return (await response.json()) as T
}

/* ---------- Response shapes ---------- */

export interface Overview {
  openQuestions: number
  resolvedQuestions: number
  archivedQuestions: number
  dispatchedForecasts: number
  completedForecasts: number
  failedForecasts: number
  timedOutForecasts: number
  needsClarification: number
  scorableForecasts: number
  latestAggregateBrier: number | null
  uninformedBaseline: number
  latestSnapshotAt: string | null
  latestAgentVersion: string | null
  openByCohort: Record<string, number>
  cohortTargets: Record<string, number>
}

export interface QuestionRow {
  id: string
  questionText: string
  category: string
  cohort: string
  status: string
  expectedResolutionDate: string
  addedBy: string
  addedByOperator: string | null
  polymarketUrl: string
  polymarketConditionId: string
  liquidityAtPickup: number | null
  forecastCount: number
  latestProbability: number | null
  latestBrier: number | null
  outcome: string | null
  createdAt: string | null
}

export interface ForecastRow {
  id: string
  runIndex: number
  sessionId: string
  status: string
  probability: number | null
  confidence: number | null
  tier: string | null
  agentVersion: string | null
  brierScore: number | null
  errorMessage: string | null
  evidenceSummary: Record<string, unknown> | null
  dispatchedAt: string | null
  completedAt: string | null
}

export interface QuestionDetail {
  question: QuestionRow
  forecasts: ForecastRow[]
  resolution: {
    outcome: string
    outcomeNumeric: number | null
    resolvedAt: string
    detectedAt: string | null
    source: string
    scorable: boolean
  } | null
}

export interface CurvePoint {
  bucket: string
  count: number
  mean_predicted: number | null
  actual_yes_rate: number | null
  yes_count: number
  lower_bound: number
  upper_bound: number
}

export interface CalibrationCurve {
  snapshotAt: string | null
  live: boolean
  points: CurvePoint[]
  total_forecasts: number
  aggregate_brier: number | null
  skill_vs_coin_flip: number | null
}

export interface CohortBrier {
  snapshotAt: string | null
  live: boolean
  items: Array<{
    cohort: string
    n: number
    mean_brier: number | null
    std_brier: number | null
    skill_vs_coin_flip: number | null
    small_sample: boolean
  }>
}

export interface ImprovementCurve {
  snapshotAt: string | null
  live: boolean
  points: Array<{
    question_id: string
    cohort: string
    original_brier: number
    latest_brier: number
    delta: number
    improved: boolean
    original_probability: number
    latest_probability: number
    agent_version_pair: (string | null)[]
    resolved_at: string | null
  }>
  n_paired_questions: number
  single_forecast_questions: number
  mean_delta: number | null
  improved_count: number
  worsened_count: number
  interpretable: boolean
  min_interpretable_n: number
}

export interface SourceContribution {
  snapshotAt: string | null
  live: boolean
  items: Array<{
    vault_type: string
    n_with: number
    n_without: number
    mean_brier_with: number | null
    mean_brier_without: number | null
    delta: number | null
    helps: boolean
    comparable: boolean
  }>
  total_forecasts: number
  min_group_size: number
  interpretation: string
}

export interface RunRow {
  id: string
  runType: string
  triggeredAt: string | null
  triggeredBy: string
  questionsDispatched: number | null
  forecastsCompleted: number | null
  forecastsFailed: number | null
  finishedAt: string | null
  isFinished: boolean
  metadata: Record<string, unknown> | null
}

/* ---------- Calls ---------- */

export const api = {
  overview: () => request<Overview>('/api/overview'),

  questions: (filters: Record<string, string> = {}) => {
    const query = new URLSearchParams(
      Object.entries(filters).filter(([, v]) => v),
    ).toString()
    return request<{ items: QuestionRow[]; total: number; page: number }>(
      `/api/questions${query ? `?${query}` : ''}`,
    )
  },

  question: (id: string) => request<QuestionDetail>(`/api/questions/${id}`),

  compare: (id: string) =>
    request<{
      original: ForecastRow | null
      latest: ForecastRow | null
      delta: number | null
      agentVersionPair: (string | null)[] | null
      reason?: string
    }>(`/api/questions/${id}/forecasts/compare`),

  addQuestion: (body: {
    polymarket_slug: string
    category: string
    cohort: string
    question_text?: string
  }) =>
    request<{ id: string | null; alreadyTracked: boolean; question: unknown }>(
      '/api/questions',
      { method: 'POST', body: JSON.stringify(body) },
    ),

  calibrationCurve: () =>
    request<CalibrationCurve>('/api/metrics/calibration_curve'),
  cohortBrier: () => request<CohortBrier>('/api/metrics/cohort_brier'),
  improvementCurve: () =>
    request<ImprovementCurve>('/api/metrics/improvement_curve'),
  sourceContribution: () =>
    request<SourceContribution>('/api/metrics/source_contribution'),

  runs: () => request<{ items: RunRow[] }>('/api/runs'),
  triggerRun: () =>
    request<Record<string, unknown>>('/api/runs/trigger', {
      method: 'POST',
      body: JSON.stringify({ run_type: 'manual' }),
    }),
}
