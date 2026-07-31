/*
 * Dashboard tests.
 *
 * Weighted toward two things: the empty states, because that is what every
 * screen shows on day one and for weeks afterward, and the honesty guarantees
 * — sample sizes, caveats, and the rule that a missing number never renders
 * as zero.
 *
 * The API module is mocked; these are render tests, and the API's own
 * behaviour is covered by test_api.py against a real database.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as fixtures from './fixtures'
import { CalibrationCurveChart } from './charts/CalibrationCurveChart'
import { CohortBrierChart } from './charts/CohortBrierChart'
import { ImprovementCurveChart } from './charts/ImprovementCurveChart'
import { SourceContributionTable } from './charts/SourceContributionTable'
import { BrierBadge } from './components/primitives'
import { brier, brierBand, delta, probability, statusLabel } from './lib/format'

vi.mock('./lib/api', async () => {
  const actual = await vi.importActual<typeof import('./lib/api')>('./lib/api')
  return {
    ...actual,
    api: {
      overview: vi.fn(),
      questions: vi.fn(),
      question: vi.fn(),
      compare: vi.fn(),
      addQuestion: vi.fn(),
      calibrationCurve: vi.fn(),
      cohortBrier: vi.fn(),
      improvementCurve: vi.fn(),
      sourceContribution: vi.fn(),
      runs: vi.fn(),
      triggerRun: vi.fn(),
    },
  }
})

const { api } = await import('./lib/api')
const {
  MetricsScreen,
  OverviewScreen,
  QuestionDetailScreen,
  QuestionsScreen,
  RunsScreen,
  ManualAddScreen,
} = await import('./components/screens')

beforeEach(() => {
  vi.clearAllMocks()
})

/* ==========================================================
 * Formatting — the "never render null as zero" rule
 * ========================================================== */

describe('formatting', () => {
  it('renders an absent number as an em dash, never as zero', () => {
    // 0.0 is a PERFECT Brier score. A null shown as 0 would display a
    // flawless forecast where there is in fact no data.
    expect(brier(null)).toBe('—')
    expect(brier(undefined)).toBe('—')
    expect(probability(null)).toBe('—')
    expect(delta(null)).toBe('—')

    expect(brier(0)).toBe('0.0000')
    expect(probability(0)).toBe('0%')
  })

  it('bands Brier against the coin-flip baseline', () => {
    expect(brierBand(0.05)).toBe('good')
    expect(brierBand(0.2)).toBe('warning')
    // 0.25 is the score for always saying 50% — at or above it the forecast
    // added nothing.
    expect(brierBand(0.25)).toBe('critical')
    expect(brierBand(null)).toBe('none')
  })

  it('does not label a clarification as a failure', () => {
    expect(statusLabel('needs_clarification')).toBe('asked for clarification')
    expect(statusLabel('failed')).toBe('failed')
  })

  it('signs deltas explicitly', () => {
    expect(delta(0.13)).toBe('+0.1300')
    expect(delta(-0.13)).toBe('-0.1300')
  })
})

describe('BrierBadge', () => {
  it('carries the quality band as text, not colour alone', () => {
    render(<BrierBadge value={0.05} />)
    expect(screen.getByText(/good/)).toBeInTheDocument()
  })

  it('renders a dash when there is no score', () => {
    const { container } = render(<BrierBadge value={null} />)
    expect(container.textContent).toBe('—')
  })
})

/* ==========================================================
 * Overview
 * ========================================================== */

describe('OverviewScreen', () => {
  it('renders the day-one empty state without crashing', async () => {
    vi.mocked(api.overview).mockResolvedValue(fixtures.emptyOverview)
    render(<OverviewScreen />)

    await waitFor(() => expect(screen.getByText('Open questions')).toBeInTheDocument())
    expect(screen.getByText('Mean Brier')).toBeInTheDocument()
    // No score yet — a dash, not a zero.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })

  it('shows failure counts beside success counts', async () => {
    vi.mocked(api.overview).mockResolvedValue(fixtures.populatedOverview)
    render(<OverviewScreen />)

    await waitFor(() => expect(screen.getByText('Completed')).toBeInTheDocument())
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText('Timed out')).toBeInTheDocument()
    expect(screen.getByText('Asked for clarification')).toBeInTheDocument()
  })

  it('says so explicitly when nothing is stuck', async () => {
    vi.mocked(api.overview).mockResolvedValue(fixtures.emptyOverview)
    render(<OverviewScreen />)

    await waitFor(() =>
      expect(
        screen.getByText(/Nothing failed, timed out, or needs clarification/),
      ).toBeInTheDocument(),
    )
  })

  it('surfaces a cohort that is short of its target', async () => {
    vi.mocked(api.overview).mockResolvedValue(fixtures.populatedOverview)
    render(<OverviewScreen />)

    // 14d has 0 open against a target of 10 — the real finding from discovery.
    await waitFor(() => expect(screen.getByText('short by 10')).toBeInTheDocument())
  })

  it('shows the mean Brier beside the coin-flip baseline', async () => {
    vi.mocked(api.overview).mockResolvedValue(fixtures.populatedOverview)
    render(<OverviewScreen />)

    await waitFor(() => expect(screen.getByText('0.1712')).toBeInTheDocument())
    expect(screen.getByText(/coin flip = 0.25/)).toBeInTheDocument()
  })

  it('reports an authorisation failure in words an operator can act on', async () => {
    const { ApiError } = await import('./lib/api')
    vi.mocked(api.overview).mockRejectedValue(new ApiError(403, 'Not an authorised operator'))
    render(<OverviewScreen />)

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/operator allowlist/),
    )
  })
})

/* ==========================================================
 * Questions
 * ========================================================== */

describe('QuestionsScreen', () => {
  it('renders an empty table with guidance', async () => {
    vi.mocked(api.questions).mockResolvedValue({ items: [], total: 0, page: 1 })
    render(<QuestionsScreen onOpen={() => {}} />)

    await waitFor(() => expect(screen.getByText('No questions match')).toBeInTheDocument())
  })

  it('renders the columns an operator scans', async () => {
    vi.mocked(api.questions).mockResolvedValue({
      items: [fixtures.questionRow],
      total: 1,
      page: 1,
    })
    render(<QuestionsScreen onOpen={() => {}} />)

    await waitFor(() =>
      expect(screen.getByText(/Strait of Hormuz/)).toBeInTheDocument(),
    )
    expect(screen.getByText('88%')).toBeInTheDocument()
    expect(screen.getByText('YES')).toBeInTheDocument()
  })

  it('offers filters above the table, not inside it', async () => {
    vi.mocked(api.questions).mockResolvedValue({ items: [], total: 0, page: 1 })
    render(<QuestionsScreen onOpen={() => {}} />)

    await waitFor(() => expect(screen.getByLabelText('status')).toBeInTheDocument())
    expect(screen.getByLabelText('cohort')).toBeInTheDocument()
    expect(screen.getByLabelText('category')).toBeInTheDocument()
  })
})

describe('QuestionDetailScreen', () => {
  it('renders the forecast history including a non-scored run', async () => {
    vi.mocked(api.question).mockResolvedValue(fixtures.questionDetail)
    render(<QuestionDetailScreen questionId="q-1" onBack={() => {}} />)

    await waitFor(() =>
      expect(screen.getByText('Forecast history')).toBeInTheDocument(),
    )
    expect(screen.getByText('62%')).toBeInTheDocument()
    // The clarification run has no probability and no Brier — dashes, not zeros.
    expect(screen.getByText('asked for clarification')).toBeInTheDocument()
  })

  it('says plainly when a question has not resolved', async () => {
    vi.mocked(api.question).mockResolvedValue({
      ...fixtures.questionDetail,
      resolution: null,
    })
    render(<QuestionDetailScreen questionId="q-1" onBack={() => {}} />)

    await waitFor(() =>
      expect(screen.getByText('Awaiting resolution')).toBeInTheDocument(),
    )
  })
})

/* ==========================================================
 * Charts — empty and populated
 * ========================================================== */

describe('CalibrationCurveChart', () => {
  it('explains itself rather than drawing an empty plot', () => {
    render(<CalibrationCurveChart data={fixtures.emptyCurve} />)
    expect(screen.getByText('No scored forecasts yet')).toBeInTheDocument()
  })

  it('renders a table twin so no value is hover-only', () => {
    render(<CalibrationCurveChart data={fixtures.populatedCurve} />)

    const table = screen.getByRole('table')
    expect(within(table).getByText('0.6-0.8')).toBeInTheDocument()
    // Every bucket appears, including the empty one.
    expect(within(table).getByText('0.0-0.2')).toBeInTheDocument()
  })

  it('shows the sample size', () => {
    render(<CalibrationCurveChart data={fixtures.populatedCurve} />)
    expect(screen.getByText(/n=20/)).toBeInTheDocument()
  })

  it('actually draws an SVG plot, not just the table', () => {
    // Guards the container stub in test-setup.ts. Without it Recharts renders
    // an empty SVG and every other chart assertion here would pass while
    // testing nothing.
    const { container } = render(
      <CalibrationCurveChart data={fixtures.populatedCurve} />,
    )
    const svg = container.querySelector('svg.recharts-surface')
    expect(svg).toBeTruthy()
    expect(container.querySelectorAll('.recharts-scatter-symbol').length).toBe(4)
  })

  it('draws a confidence interval on every plotted bucket', () => {
    /*
     * At n=3 with every question resolving YES the Wilson upper bound is
     * exactly 1.0, so that arm has zero length. Recharts logs a duplicate-key
     * warning for zero-length arms; this asserts the bars are still all
     * present, since the warning says children "may be duplicated and/or
     * omitted" and an omitted interval would overstate the chart's precision.
     */
    const { container } = render(
      <CalibrationCurveChart data={fixtures.populatedCurve} />,
    )
    expect(container.querySelectorAll('.recharts-errorBar').length).toBe(4)
  })

  it('plots the diagonal reference alongside the agent', () => {
    const { container } = render(
      <CalibrationCurveChart data={fixtures.populatedCurve} />,
    )
    expect(container.querySelector('.recharts-line')).toBeTruthy()
    // Two marks means a legend is mandatory — identity is never colour-alone.
    expect(screen.getByText('perfect calibration')).toBeInTheDocument()
    expect(screen.getByText('the agent')).toBeInTheDocument()
  })
})

describe('CohortBrierChart', () => {
  it('explains itself when no cohort has resolved', () => {
    render(<CohortBrierChart data={fixtures.emptyCohorts} />)
    expect(
      screen.getByText('No cohort has a resolved forecast yet'),
    ).toBeInTheDocument()
  })

  it('renders every cohort with its n', () => {
    render(<CohortBrierChart data={fixtures.populatedCohorts} />)

    const table = screen.getByRole('table')
    expect(within(table).getByText('0.1178')).toBeInTheDocument()
    expect(within(table).getByText('0.2000')).toBeInTheDocument()
  })

  it('flags a small sample', () => {
    render(<CohortBrierChart data={fixtures.populatedCohorts} />)
    expect(screen.getAllByText(/small n/).length).toBeGreaterThan(0)
  })

  it('draws one bar per cohort plus the coin-flip reference', () => {
    const { container } = render(<CohortBrierChart data={fixtures.populatedCohorts} />)
    expect(container.querySelectorAll('.recharts-bar-rectangle').length).toBe(4)
    expect(container.querySelector('[class*="reference-line"]')).toBeTruthy()
    expect(screen.getByText('always saying 50%')).toBeInTheDocument()
  })

  it('keeps the coin-flip line visible when every cohort beats it', () => {
    /*
     * The regression this guards: with a data-scaled y-domain, a chart where
     * every score is below 0.25 drops the reference line — losing the
     * reference exactly when it is good news.
     */
    const allGood: typeof fixtures.populatedCohorts = {
      ...fixtures.populatedCohorts,
      items: fixtures.populatedCohorts.items.map((i) => ({
        ...i,
        mean_brier: 0.05,
      })),
    }
    const { container } = render(<CohortBrierChart data={allGood} />)
    expect(container.querySelector('[class*="reference-line"]')).toBeTruthy()
    expect(screen.getByText('always saying 50%')).toBeInTheDocument()
  })
})

describe('ImprovementCurveChart', () => {
  it('explains itself when nothing has been re-forecast', () => {
    render(<ImprovementCurveChart data={fixtures.emptyImprovement} />)
    expect(
      screen.getByText('No question has been forecast twice and resolved yet'),
    ).toBeInTheDocument()
  })

  it('renders the noise caveat below the interpretability threshold', () => {
    // 3 paired questions against a threshold of 10 — the chart still renders,
    // but never bare.
    render(<ImprovementCurveChart data={fixtures.populatedImprovement} />)
    expect(screen.getByText(/the sign is noise, not a trend/)).toBeInTheDocument()
  })

  it('labels each row better or worse, not colour alone', () => {
    render(<ImprovementCurveChart data={fixtures.populatedImprovement} />)
    expect(screen.getAllByText(/better/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/worse/).length).toBeGreaterThan(0)
  })

  it('reports questions excluded for having a single forecast', () => {
    render(<ImprovementCurveChart data={fixtures.populatedImprovement} />)
    expect(screen.getByText(/2 excluded for having a single forecast/)).toBeInTheDocument()
  })

  it('draws a diverging bar per paired question around a zero baseline', () => {
    const { container } = render(
      <ImprovementCurveChart data={fixtures.populatedImprovement} />,
    )
    expect(container.querySelectorAll('.recharts-bar-rectangle').length).toBe(3)
    // The zero line is what makes the sign readable without the legend.
    expect(container.querySelector('.recharts-reference-line')).toBeTruthy()
  })
})

describe('SourceContributionTable', () => {
  it('explains itself when there is nothing to attribute', () => {
    render(<SourceContributionTable data={fixtures.emptySources} />)
    expect(
      screen.getByText('No scored forecasts to attribute yet'),
    ).toBeInTheDocument()
  })

  it('always carries the causal caveat', () => {
    // The number outlives anyone's memory of the caveat, so it travels with
    // the data rather than living in the docs.
    render(<SourceContributionTable data={fixtures.populatedSources} />)
    expect(screen.getByText(/Observational, not causal/)).toBeInTheDocument()
  })

  it('marks a vault whose comparison group is too small', () => {
    render(<SourceContributionTable data={fixtures.populatedSources} />)
    expect(screen.getAllByText(/groups under 5/).length).toBeGreaterThan(0)
  })

  it('lists every vault, including ones that contributed to nothing', () => {
    render(<SourceContributionTable data={fixtures.populatedSources} />)
    expect(screen.getByText('mapping')).toBeInTheDocument()
    expect(screen.getByText('reactive_search')).toBeInTheDocument()
  })
})

/* ==========================================================
 * The demonstration state — zero and three data points
 *
 * Not edge cases. Markets take weeks to settle, so a handful of points is the
 * normal shape of this data for the system's first month, and that is the
 * state it will be shown in. Every chart has to stay legible and honest
 * there, not only at n=20.
 * ========================================================== */

describe('presenting with almost no data', () => {
  it('draws a calibration curve from three points without breaking', () => {
    const { container } = render(
      <CalibrationCurveChart data={fixtures.threePointCurve} />,
    )
    expect(container.querySelectorAll('.recharts-scatter-symbol').length).toBe(3)
    expect(container.querySelector('.recharts-line')).toBeTruthy()
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('states the sample size at n=3', () => {
    render(<CalibrationCurveChart data={fixtures.threePointCurve} />)
    expect(screen.getByText(/n=3/)).toBeInTheDocument()
  })

  it('keeps empty buckets visible in the table at n=3', () => {
    // Three rows would look complete. Five rows, two marked empty, says what
    // the agent has and has not been asked.
    render(<CalibrationCurveChart data={fixtures.threePointCurve} />)
    const table = screen.getByRole('table')
    expect(within(table).getByText('0.4-0.6')).toBeInTheDocument()
    expect(within(table).getByText('0.6-0.8')).toBeInTheDocument()
  })

  it('renders a cohort chart where one cohort has no data at all', () => {
    const { container } = render(
      <CohortBrierChart data={fixtures.threePointCohorts} />,
    )
    expect(container.querySelector('[class*="reference-line"]')).toBeTruthy()
    expect(within(screen.getByRole('table')).getByText('30-45d')).toBeInTheDocument()
    expect(screen.getAllByText(/small n/).length).toBeGreaterThan(0)
  })

  it('renders an improvement chart from one paired question, with the caveat', () => {
    render(<ImprovementCurveChart data={fixtures.onePointImprovement} />)
    expect(screen.getByText(/the sign is noise, not a trend/)).toBeInTheDocument()
    expect(
      screen.getByText(/2 excluded for having a single forecast/),
    ).toBeInTheDocument()
  })

  it('shows a complete overview when forecasts exist but none have resolved', async () => {
    // The literal state on demo day if no market has settled yet.
    vi.mocked(api.overview).mockResolvedValue({
      ...fixtures.emptyOverview,
      openQuestions: 28,
      completedForecasts: 5,
      scorableForecasts: 0,
      latestAgentVersion: '0.5.0-sprint26+55e8093',
      openByCohort: { '7d': 10, '14d': 10, '30-45d': 8 },
    })
    render(<OverviewScreen />)

    await waitFor(() => expect(screen.getByText('28')).toBeInTheDocument())
    expect(screen.getByText('Mean Brier')).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
    expect(
      screen.getByText(/Nothing failed, timed out, or needs clarification/),
    ).toBeInTheDocument()
  })

  it('never renders a missing score as zero', () => {
    // 0.0 is a PERFECT Brier score. An absent one shown as 0 would claim a
    // flawless forecaster on stage.
    render(<CohortBrierChart data={fixtures.threePointCohorts} />)
    const emptyRow = screen
      .getByRole('table')
      .querySelectorAll('tbody tr')[2] // 30-45d, n=0
    expect(emptyRow.textContent).toContain('—')
    expect(emptyRow.textContent).not.toMatch(/0\.0000/)
  })
})

/* ==========================================================
 * Metrics screen
 * ========================================================== */

describe('MetricsScreen', () => {
  it('renders all four metric cards', async () => {
    vi.mocked(api.calibrationCurve).mockResolvedValue(fixtures.populatedCurve)
    vi.mocked(api.cohortBrier).mockResolvedValue(fixtures.populatedCohorts)
    vi.mocked(api.improvementCurve).mockResolvedValue(fixtures.populatedImprovement)
    vi.mocked(api.sourceContribution).mockResolvedValue(fixtures.populatedSources)

    render(<MetricsScreen />)

    await waitFor(() =>
      expect(screen.getByText('Calibration curve')).toBeInTheDocument(),
    )
    expect(screen.getByText('Brier by horizon')).toBeInTheDocument()
    expect(screen.getByText('Did re-forecasting help?')).toBeInTheDocument()
    expect(screen.getByText('Vault contribution')).toBeInTheDocument()
  })

  it('renders every card in its empty state without crashing', async () => {
    vi.mocked(api.calibrationCurve).mockResolvedValue(fixtures.emptyCurve)
    vi.mocked(api.cohortBrier).mockResolvedValue(fixtures.emptyCohorts)
    vi.mocked(api.improvementCurve).mockResolvedValue(fixtures.emptyImprovement)
    vi.mocked(api.sourceContribution).mockResolvedValue(fixtures.emptySources)

    render(<MetricsScreen />)

    await waitFor(() =>
      expect(screen.getByText('No scored forecasts yet')).toBeInTheDocument(),
    )
    expect(
      screen.getByText('No cohort has a resolved forecast yet'),
    ).toBeInTheDocument()
  })
})

/* ==========================================================
 * Runs and manual add
 * ========================================================== */

describe('RunsScreen', () => {
  it('renders an empty history with guidance', async () => {
    vi.mocked(api.runs).mockResolvedValue({ items: [] })
    render(<RunsScreen />)

    await waitFor(() => expect(screen.getByText('Nothing has run yet')).toBeInTheDocument())
  })

  it('distinguishes a finished run from one still going', async () => {
    vi.mocked(api.runs).mockResolvedValue({ items: fixtures.runRows })
    render(<RunsScreen />)

    await waitFor(() => expect(screen.getByText('weekly_reforecast')).toBeInTheDocument())
    expect(screen.getByText('still running')).toBeInTheDocument()
  })

  it('states that the trigger cannot dispatch forecasts', async () => {
    vi.mocked(api.runs).mockResolvedValue({ items: [] })
    render(<RunsScreen />)

    await waitFor(() =>
      expect(
        screen.getByText(/Dispatching forecasts costs tokens/),
      ).toBeInTheDocument(),
    )
  })
})

describe('ManualAddScreen', () => {
  it('renders the form with the fields the operator supplies', () => {
    render(<ManualAddScreen />)
    expect(screen.getByLabelText('market slug')).toBeInTheDocument()
    expect(screen.getByLabelText('category')).toBeInTheDocument()
    expect(screen.getByLabelText('cohort')).toBeInTheDocument()
  })

  it('disables submission until a slug is entered', () => {
    render(<ManualAddScreen />)
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
  })

  it('explains where the slug comes from', () => {
    render(<ManualAddScreen />)
    expect(screen.getByText(/last path segment of the market URL/)).toBeInTheDocument()
  })
})
