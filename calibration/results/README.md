# Results

Measurements exported from the calibration database. Regenerate with:

```bash
cd calibration
python -m calibration.cli export
```

Everything here is derived from the database, so the folder can be deleted and
rebuilt at any time. Nothing is hand-edited.

## Files

| File | What it holds |
|---|---|
| `summary.json` | Headline numbers, plus **how to read them** — start here |
| `forecasts.csv` / `.json` | One row per forecast: probability, confidence, Brier score, which vaults it used |
| `questions.csv` | One row per tracked question, with its outcome once settled |
| `calibration_curve.csv` | The five probability buckets: predicted vs. actual |
| `cohort_brier.csv` | Accuracy split by how far ahead the question looks |
| `improvement.csv` | First forecast vs. latest, per resolved question |
| `source_contribution.csv` | Per-vault comparison — forecasts that used it vs. those that did not |
| `runs.csv` | What ran, when, and what it was for |
| `metrics.json` | Every metric payload with its structure intact |

## Reading the numbers

**Brier score — lower is better.** It is the squared distance between the
forecast and what happened: say 0.9 on something that happens and you score
0.01; say 0.9 on something that does not and you score 0.81.

**0.25 is the bar.** That is the score for always saying "50%". A forecaster
who cannot beat 0.25 is not adding anything, so every table here reports the
baseline alongside the score.

**An empty file is not a broken file.** A forecast is scored only when its
Polymarket market settles, which is weeks after the forecast was made. Empty
tables early on are the expected state — `summary.json` says so explicitly,
and every CSV keeps its header row so the difference between "measured, no
data yet" and "something failed" stays visible.

**`n` is on every aggregate.** A mean over three questions and a mean over
forty look identical and mean entirely different things. Where a sample is too
small to read as a trend, the file says so rather than leaving the reader to
work it out.

## What is deliberately excluded

Forecasts that `failed`, `timed_out`, or came back as `needs_clarification`
are excluded from every score — but they are counted in
`summary.json → forecasts_by_status`. A metric that silently drops its
failures overstates itself.

`needs_clarification` in particular is **not** a failure: the agent was asked
something ambiguous and asked back, which is correct behaviour. This version
does not answer clarifications, so those forecasts are closed unscored.

Questions whose market settled to `AMBIGUOUS` are recorded but never scored.
There is no ground truth to score against, and treating ambiguous as "no"
would silently mark every attached forecast wrong.
