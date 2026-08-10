# calibration_plan.md — MOVED
> Domain: D — Calibration
> Type: Pointer
> Last updated: 2026-07-29

**The calibration system now lives at the repository root, in `/calibration/`.**

It was moved out of `data-pipeline/` on 2026-07-29 because it is not part of
the pipeline. It imports nothing from `data-pipeline/`, `server/`, or
`client/` — a property enforced by a test, not by convention — and keeping it
nested here implied a dependency that does not exist.

| What | Where |
|---|---|
| The plan — design, decisions, sprint status, open questions | `/calibration/docs/calibration_plan.md` |
| How to run it, and how to stop it | `/calibration/docs/OPERATOR_RUNBOOK.md` |
| Overview | `/calibration/README.md` |
| Code | `/calibration/src/calibration/` |
| Tests | `/calibration/tests/` |
| Operator dashboard | `/calibration/dashboard/` |

## What it is, in one paragraph

A harness that measures whether the agent's probabilities mean what they say.
It takes Polymarket questions with known future answers, submits them to the
**existing, unmodified** agent through the existing `forecastQueries →
sessionResults` Firestore flow, waits for the market to settle, and computes
Brier scores and a calibration curve. It writes to its own Postgres database
and to exactly two Firestore documents per forecast, both of which it creates
itself and both of which carry `userId: "calibration-runner"`.

## Why this file remains

Domain D is referenced from the repository's documentation index; this pointer
keeps that reference working. It deliberately carries no content — the plan
moved, and maintaining two copies would guarantee they eventually disagree.
