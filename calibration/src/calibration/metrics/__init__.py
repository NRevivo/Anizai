"""
Metrics — turning resolved forecasts into numbers about the agent.

Every module here is a pure function over rows already in Postgres. No
network, no Firestore, no LLM. That is what makes the numbers reproducible:
running the metrics twice on the same data gives the same answer, and a
disagreement between two runs is a bug rather than a fact about the world.

The inclusion rule is enforced in exactly one place — `repos.forecasts.
list_scorable` — and every module here consumes its output. A second copy of
that predicate would eventually disagree with the first, and two Brier scores
computed from the same data that differ is worse than no Brier score at all.

Modules:
    brier.py                — the primary score
    calibration_curve.py    — predicted vs. actual, in five buckets
    cohort_brier.py         — score split by resolution horizon
    improvement_curve.py    — did re-forecasting help?
    source_contribution.py  — which vault is actually predictive?
    snapshots.py            — persist all of the above
"""
