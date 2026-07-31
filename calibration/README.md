# Calibration Harness

**Standalone.** This directory does not import from `data-pipeline/`,
`server/`, or `client/`, and nothing in those imports from here. It has its
own dependencies, its own database, its own tests, and its own dashboard. That
separation is enforced by a test, not by convention — see
[Isolation](#isolation) below.

---

## What it does

It measures whether the Anizai agent's probabilities mean what they say.

A forecaster who says "70%" should be right about 70% of the time. Nobody had
ever checked. This harness checks it:

1. **Collects** questions from Polymarket — real questions with real deadlines,
   which means reality eventually supplies the answer.
2. **Asks the agent**, through the same Firestore queue the product uses. The
   agent is not modified and is not told this is a test.
3. **Waits** for the market to settle.
4. **Scores** the agent — Brier score, calibration curve, per-horizon
   accuracy, and whether re-forecasting closer to the event helps.

```
Polymarket ──▶ discover ──▶ dispatch ──▶ [ the existing agent ] ──▶ harvest
                                                                      │
                              resolve ◀── Polymarket settles ◀────────┘
                                 │
                              metrics ──▶ dashboard
```

---

## Layout

```
calibration/
├── README.md              you are here
├── requirements.txt       Python dependencies (all already used elsewhere in the repo)
├── pytest.ini             puts src/ on the path; stops conftest collection at this dir
├── docs/
│   ├── OPERATOR_RUNBOOK.md    how to run it, and how to STOP it
│   └── calibration_plan.md    the full design, decisions, and sprint status
├── src/calibration/       the Python package
│   ├── cli.py             the operator entry point
│   ├── config.py          every environment variable, and the kill switch
│   ├── db.py              connection pool for the calibration database
│   ├── models.py          typed boundary between SQL rows and Python
│   ├── firestore_client.py  the ONLY module that touches Firestore
│   ├── auth.py            operator authentication for the API
│   ├── server.py          FastAPI operator API
│   ├── evidence_projection.py  what the agent used, compressed for storage
│   ├── polymarket/        market discovery and resolution detection
│   ├── repos/             one module per database table
│   ├── services/          discovery · dispatch · harvest · resolution
│   ├── metrics/           Brier · calibration curve · cohorts · improvement
│   └── sql/init.sql       the schema
├── tests/                 451 tests
└── dashboard/             separate Vite + React operator UI
```

---

## Quick start

```bash
cd calibration
pip install -r requirements.txt

export CALIBRATION_DATABASE_URL=postgresql://user:pass@localhost:5432/anizai_calibration

python -m calibration.cli check-config      # validate settings, connect to nothing
python -m calibration.cli init-db           # create the schema (idempotent)
python -m calibration.cli discover --dry-run  # see what it WOULD add; writes nothing
python -m calibration.cli discover          # add questions
```

Everything above is local and free. The step that costs money is `dispatch`,
which sends questions to the live agent — it refuses to run against live
Firestore unless you pass `--allow-live`, and it wants to know why:

```bash
python -m calibration.cli dispatch --allow-live \
  --purpose "..." --evidence-caveat "..."
python -m calibration.cli harvest           # collect results
python -m calibration.cli resolve           # check Polymarket, score what settled
python -m calibration.cli compute-metrics   # aggregate
```

**Read [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md) before the first
live run.** It starts with how to stop the system, which is the section you
will want first if anything goes wrong.

---

## Isolation

The harness measures production, so it must not perturb production. Seven
rules, all enforced by `tests/test_calibration/test_isolation.py`, which
parses the source and fails on violation:

| | |
|---|---|
| **N1** | No changes to the agent |
| **N2** | No changes to the BFF (`server/`) |
| **N3** | No changes to the frontend (`client/`) |
| **N4** | No writes to any vault; no imports from `data-pipeline/` at all |
| **N5** | Never writes to `sessionResults` or a session subcollection |
| **N6** | Never reads, modifies, or deletes a session it did not create |
| **N7** | No shared quota, user record, or collection semantics with real users |

Mechanically checked, not merely intended:

- **No forbidden imports.** No module here may import `agent`, `persistence`,
  `processing`, `ingestion`, `orchestration`, `utils`, or `config`.
- **Firestore lives in one file.** Only `firestore_client.py` may perform a
  document operation, and a test asserts it issues no `.where()`, no
  `.list_documents()`, and no unscoped `.stream()` — so the harvester
  structurally cannot reach a document it did not create.
- **Writes only to `calibration_*` tables.** Every SQL literal in the package
  is scanned.
- **The database URL is checked.** A connection URL naming the pipeline's
  vault database (`anizai`) is refused before a connection opens.

Every session it creates carries `userId: "calibration-runner"` and a
`metadata.calibration` block, so its footprint is identifiable and removable.

---

## The kill switch

```bash
export CALIBRATION_ENABLED=false
```

Every command and endpoint refuses, checked **before** any I/O — so nothing is
left half-done. Two further independent levers (pause the schedulers, revoke
the IAM binding) are in the runbook.

---

## Tests

```bash
cd calibration
pytest                      # 451 tests
```

Tests needing a backend skip cleanly when it is absent, so this passes on a
machine with neither Postgres nor the Firestore emulator. To run everything:

```bash
docker run -d --name calibration-test-db \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=anizai_calibration \
  -p 55432:5432 postgres:16-alpine
export CALIBRATION_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/anizai_calibration

cd ../server/firebase && npx firebase-tools emulators:start --only firestore --project anizai-ai
export FIRESTORE_EMULATOR_HOST=localhost:8080
```

> **Use a different database for tests than for real data.** The integration
> fixtures truncate every `calibration_*` table. Pointing both at one database
> deletes forecasts that cost money to produce — learned the hard way on
> 2026-07-29.

Dashboard:

```bash
cd dashboard && npm install && npm test && npm run build
```

---

## Status

| Phase | What | State |
|---|---|---|
| 10A | Schema + Polymarket adapter | Done |
| 10B | Dispatch + harvest bridge | Done — verified against the live agent |
| 10C | Scoring + metrics | Done |
| 10E | Operator API + dashboard | Done — runs locally |
| 10D | Cloud automation | **Built, not switched on** — descoped 2026-07-31 |

Phase 10D's code exists and is tested (`/tasks/*` endpoints, OIDC
verification, `Dockerfile`, `infrastructure/provision.sh`), but no cloud
resource is provisioned. The system runs against a local Postgres and exports
to `results/`, which is what the deliverable actually needs. Turning the cloud
on later is a provisioning decision, not a development one — see
[`infrastructure/README.md`](infrastructure/README.md).

## Dispatch limits

Dispatch is the only operation that costs anything: each forecast is a real
request against an OpenAI quota shared with the live product.

| | Default | |
|---|---|---|
| `CALIBRATION_MAX_FORECASTS_PER_RUN` | **3** | One run |
| `CALIBRATION_MAX_FORECASTS_PER_DAY` | **30** | Rolling 24h, across all runs |

The daily ceiling is the one that matters. A caller that dispatches 3, fails
to harvest, and retries every five minutes respects the per-run cap perfectly
while emitting 864 forecasts a day — so the guard counts dispatches in a time
window rather than trusting the caller. Hitting it raises an error rather than
quietly dispatching nothing, because getting there means something is looping.

**Coordinate before any batch larger than a few questions.** The agent is
shared.

**First live run: 2026-07-29.** Five questions through the real agent, 5/5
completed, no failures, 55–210 s each. It exposed one defect — the evidence
projection was written against source names the agent does not use, so it
reported 15 pieces of evidence and zero contributing vaults simultaneously.
Fixed, with a regression test built on the real payload.

Full history, design decisions, and open questions:
[`docs/calibration_plan.md`](docs/calibration_plan.md).
