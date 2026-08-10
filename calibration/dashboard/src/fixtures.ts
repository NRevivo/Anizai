/*
 * Fixtures for the dashboard tests.
 *
 * Two families on purpose: `empty*` for the state the system is actually in
 * on day one, and populated ones for the state it reaches later. The empty
 * fixtures are not an edge case here — every screen renders empty until the
 * first question resolves, which for the 30-45d cohort is over a month away.
 */

import type {
  CalibrationCurve,
  CohortBrier,
  ImprovementCurve,
  Overview,
  QuestionDetail,
  QuestionRow,
  RunRow,
  SourceContribution,
} from './lib/api'

export const emptyOverview: Overview = {
  openQuestions: 0,
  resolvedQuestions: 0,
  archivedQuestions: 0,
  dispatchedForecasts: 0,
  completedForecasts: 0,
  failedForecasts: 0,
  timedOutForecasts: 0,
  needsClarification: 0,
  scorableForecasts: 0,
  latestAggregateBrier: null,
  uninformedBaseline: 0.25,
  latestSnapshotAt: null,
  latestAgentVersion: null,
  openByCohort: { '7d': 0, '14d': 0, '30-45d': 0 },
  cohortTargets: { '7d': 10, '14d': 10, '30-45d': 8 },
}

export const populatedOverview: Overview = {
  ...emptyOverview,
  openQuestions: 18,
  resolvedQuestions: 12,
  dispatchedForecasts: 3,
  completedForecasts: 22,
  failedForecasts: 1,
  timedOutForecasts: 0,
  needsClarification: 1,
  scorableForecasts: 20,
  latestAggregateBrier: 0.1712,
  latestSnapshotAt: '2026-07-27T10:00:00+00:00',
  latestAgentVersion: '0.5.0-sprint26+55e8093',
  openByCohort: { '7d': 10, '14d': 0, '30-45d': 8 },
}

export const emptyCurve: CalibrationCurve = {
  snapshotAt: null,
  live: true,
  total_forecasts: 0,
  aggregate_brier: null,
  skill_vs_coin_flip: null,
  points: [
    ['0.0-0.2'],
    ['0.2-0.4'],
    ['0.4-0.6'],
    ['0.6-0.8'],
    ['0.8-1.0'],
  ].map(([bucket]) => ({
    bucket,
    count: 0,
    mean_predicted: null,
    actual_yes_rate: null,
    yes_count: 0,
    lower_bound: 0,
    upper_bound: 1,
  })),
}

export const populatedCurve: CalibrationCurve = {
  snapshotAt: '2026-07-27T10:00:00+00:00',
  live: false,
  total_forecasts: 20,
  aggregate_brier: 0.1712,
  skill_vs_coin_flip: 0.3152,
  points: [
    {
      bucket: '0.0-0.2',
      count: 0,
      mean_predicted: null,
      actual_yes_rate: null,
      yes_count: 0,
      lower_bound: 0,
      upper_bound: 1,
    },
    {
      bucket: '0.2-0.4',
      count: 2,
      mean_predicted: 0.3,
      actual_yes_rate: 0,
      yes_count: 0,
      lower_bound: 0,
      upper_bound: 0.658,
    },
    {
      bucket: '0.4-0.6',
      count: 8,
      mean_predicted: 0.515,
      actual_yes_rate: 0.5,
      yes_count: 4,
      lower_bound: 0.215,
      upper_bound: 0.785,
    },
    {
      bucket: '0.6-0.8',
      count: 7,
      mean_predicted: 0.676,
      actual_yes_rate: 0.714,
      yes_count: 5,
      lower_bound: 0.359,
      upper_bound: 0.923,
    },
    {
      bucket: '0.8-1.0',
      count: 3,
      mean_predicted: 0.873,
      actual_yes_rate: 1,
      yes_count: 3,
      lower_bound: 0.438,
      upper_bound: 1,
    },
  ],
}

export const emptyCohorts: CohortBrier = {
  snapshotAt: null,
  live: true,
  items: ['7d', '14d', '30-45d', 'all'].map((cohort) => ({
    cohort,
    n: 0,
    mean_brier: null,
    std_brier: null,
    skill_vs_coin_flip: null,
    small_sample: false,
  })),
}

export const populatedCohorts: CohortBrier = {
  snapshotAt: '2026-07-27T10:00:00+00:00',
  live: false,
  items: [
    { cohort: '7d', n: 7, mean_brier: 0.1178, std_brier: 0.1052, skill_vs_coin_flip: 0.5288, small_sample: true },
    { cohort: '14d', n: 6, mean_brier: 0.2, std_brier: 0.1193, skill_vs_coin_flip: 0.2, small_sample: true },
    { cohort: '30-45d', n: 7, mean_brier: 0.1999, std_brier: 0.1114, skill_vs_coin_flip: 0.2004, small_sample: true },
    { cohort: 'all', n: 20, mean_brier: 0.1712, std_brier: 0.1184, skill_vs_coin_flip: 0.3152, small_sample: false },
  ],
}

export const emptyImprovement: ImprovementCurve = {
  snapshotAt: null,
  live: true,
  points: [],
  n_paired_questions: 0,
  single_forecast_questions: 0,
  mean_delta: null,
  improved_count: 0,
  worsened_count: 0,
  interpretable: false,
  min_interpretable_n: 10,
}

export const populatedImprovement: ImprovementCurve = {
  snapshotAt: '2026-07-27T10:00:00+00:00',
  live: false,
  n_paired_questions: 3,
  single_forecast_questions: 2,
  mean_delta: 0.1372,
  improved_count: 2,
  worsened_count: 1,
  interpretable: false,
  min_interpretable_n: 10,
  points: [
    {
      question_id: 'q1', cohort: '7d', original_brier: 0.1444, latest_brier: 0.0144,
      delta: 0.13, improved: true, original_probability: 0.62, latest_probability: 0.88,
      agent_version_pair: ['v1', 'v2'], resolved_at: '2026-08-01T00:00:00+00:00',
    },
    {
      question_id: 'q2', cohort: '14d', original_brier: 0.1764, latest_brier: 0.0676,
      delta: 0.1088, improved: true, original_probability: 0.58, latest_probability: 0.74,
      agent_version_pair: ['v1', 'v2'], resolved_at: '2026-08-02T00:00:00+00:00',
    },
    {
      question_id: 'q3', cohort: '30-45d', original_brier: 0.09, latest_brier: 0.25,
      delta: -0.16, improved: false, original_probability: 0.7, latest_probability: 0.5,
      agent_version_pair: ['v1', 'v2'], resolved_at: '2026-08-03T00:00:00+00:00',
    },
  ],
}

const CAVEAT =
  'Observational, not causal. Vaults are not randomly assigned to questions, ' +
  'so a vault’s delta may reflect which questions it fires on rather than ' +
  'what it contributes. Do not de-prioritise a vault on this number alone.'

export const emptySources: SourceContribution = {
  snapshotAt: null,
  live: true,
  total_forecasts: 0,
  min_group_size: 5,
  interpretation: CAVEAT,
  items: ['knowledge', 'social', 'momentum', 'mapping', 'reactive_search'].map(
    (vault_type) => ({
      vault_type,
      n_with: 0,
      n_without: 0,
      mean_brier_with: null,
      mean_brier_without: null,
      delta: null,
      helps: false,
      comparable: false,
    }),
  ),
}

export const populatedSources: SourceContribution = {
  ...emptySources,
  snapshotAt: '2026-07-27T10:00:00+00:00',
  live: false,
  total_forecasts: 20,
  items: [
    { vault_type: 'knowledge', n_with: 15, n_without: 5, mean_brier_with: 0.14, mean_brier_without: 0.26, delta: -0.12, helps: true, comparable: true },
    { vault_type: 'social', n_with: 8, n_without: 12, mean_brier_with: 0.19, mean_brier_without: 0.16, delta: 0.03, helps: false, comparable: true },
    { vault_type: 'momentum', n_with: 3, n_without: 17, mean_brier_with: 0.1, mean_brier_without: 0.18, delta: -0.08, helps: true, comparable: false },
    { vault_type: 'mapping', n_with: 0, n_without: 20, mean_brier_with: null, mean_brier_without: 0.17, delta: null, helps: false, comparable: false },
    { vault_type: 'reactive_search', n_with: 0, n_without: 20, mean_brier_with: null, mean_brier_without: 0.17, delta: null, helps: false, comparable: false },
  ],
}

/*
 * Three resolved forecasts — the state the system will most likely be shown
 * in. Not an edge case: markets take weeks to settle, so "a handful of
 * points" is the normal shape of this data for its first month, and every
 * chart has to stay legible and honest there rather than only at n=20.
 */
export const threePointCurve: CalibrationCurve = {
  snapshotAt: '2026-08-14T10:00:00+00:00',
  live: false,
  total_forecasts: 3,
  aggregate_brier: 0.1467,
  skill_vs_coin_flip: 0.4133,
  points: [
    { bucket: '0.0-0.2', count: 1, mean_predicted: 0.1, actual_yes_rate: 0, yes_count: 0, lower_bound: 0, upper_bound: 0.793 },
    { bucket: '0.2-0.4', count: 1, mean_predicted: 0.3, actual_yes_rate: 0, yes_count: 0, lower_bound: 0, upper_bound: 0.793 },
    { bucket: '0.4-0.6', count: 0, mean_predicted: null, actual_yes_rate: null, yes_count: 0, lower_bound: 0, upper_bound: 1 },
    { bucket: '0.6-0.8', count: 0, mean_predicted: null, actual_yes_rate: null, yes_count: 0, lower_bound: 0, upper_bound: 1 },
    { bucket: '0.8-1.0', count: 1, mean_predicted: 0.88, actual_yes_rate: 1, yes_count: 1, lower_bound: 0.207, upper_bound: 1 },
  ],
}

export const threePointCohorts: CohortBrier = {
  snapshotAt: '2026-08-14T10:00:00+00:00',
  live: false,
  items: [
    { cohort: '7d', n: 1, mean_brier: 0.01, std_brier: null, skill_vs_coin_flip: 0.96, small_sample: true },
    { cohort: '14d', n: 2, mean_brier: 0.215, std_brier: 0.185, skill_vs_coin_flip: 0.14, small_sample: true },
    { cohort: '30-45d', n: 0, mean_brier: null, std_brier: null, skill_vs_coin_flip: null, small_sample: false },
    { cohort: 'all', n: 3, mean_brier: 0.1467, std_brier: 0.1683, skill_vs_coin_flip: 0.4133, small_sample: true },
  ],
}

/** A single paired question — the weakest possible improvement signal. */
export const onePointImprovement: ImprovementCurve = {
  ...emptyImprovement,
  snapshotAt: '2026-08-14T10:00:00+00:00',
  live: false,
  n_paired_questions: 1,
  single_forecast_questions: 2,
  mean_delta: 0.13,
  improved_count: 1,
  worsened_count: 0,
  interpretable: false,
  points: [
    {
      question_id: 'q1', cohort: '7d', original_brier: 0.1444, latest_brier: 0.0144,
      delta: 0.13, improved: true, original_probability: 0.62, latest_probability: 0.88,
      agent_version_pair: ['0.5.0-sprint26', '0.5.0-sprint26'],
      resolved_at: '2026-08-04T00:00:00+00:00',
    },
  ],
}

export const questionRow: QuestionRow = {
  id: 'q-1',
  questionText: 'Strait of Hormuz traffic returns to normal by July 31?',
  category: 'geopolitical',
  cohort: '7d',
  status: 'resolved',
  expectedResolutionDate: '2026-07-31',
  addedBy: 'auto',
  addedByOperator: null,
  polymarketUrl: 'https://polymarket.com/event/hormuz',
  polymarketConditionId: '0xabc',
  liquidityAtPickup: 20531119,
  forecastCount: 2,
  latestProbability: 0.88,
  latestBrier: 0.0144,
  outcome: 'YES',
  createdAt: '2026-07-20T00:00:00+00:00',
}

export const questionDetail: QuestionDetail = {
  question: questionRow,
  forecasts: [
    {
      id: 'f-0', runIndex: 0, sessionId: 'cal_aaa', status: 'completed',
      probability: 0.62, confidence: 0.7, tier: 'tier_1',
      agentVersion: '0.5.0-sprint26+55e8093', brierScore: 0.1444,
      errorMessage: null,
      evidenceSummary: { evidence_count_total: 3 },
      dispatchedAt: '2026-07-20T10:00:00+00:00',
      completedAt: '2026-07-20T10:01:00+00:00',
    },
    {
      id: 'f-1', runIndex: 1, sessionId: 'cal_bbb', status: 'needs_clarification',
      probability: null, confidence: null, tier: null,
      agentVersion: null, brierScore: null, errorMessage: null,
      evidenceSummary: null,
      dispatchedAt: '2026-07-26T10:00:00+00:00', completedAt: '2026-07-26T10:01:00+00:00',
    },
  ],
  resolution: {
    outcome: 'YES',
    outcomeNumeric: 1,
    resolvedAt: '2026-07-31T12:00:00+00:00',
    detectedAt: '2026-07-31T13:00:00+00:00',
    source: 'polymarket_clob',
    scorable: true,
  },
}

export const runRows: RunRow[] = [
  {
    id: 'r-1', runType: 'weekly_reforecast',
    triggeredAt: '2026-07-27T02:00:00+00:00', triggeredBy: 'cloud_scheduler',
    questionsDispatched: 12, forecastsCompleted: 10, forecastsFailed: 1,
    finishedAt: '2026-07-27T02:04:00+00:00', isFinished: true, metadata: null,
  },
  {
    id: 'r-2', runType: 'manual',
    triggeredAt: '2026-07-27T09:00:00+00:00', triggeredBy: 'operator@example.com',
    questionsDispatched: null, forecastsCompleted: null, forecastsFailed: null,
    finishedAt: null, isFinished: false, metadata: null,
  },
]
