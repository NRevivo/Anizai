# Calibration — Operator Runbook

> Written for someone who has never seen this code.
> If you are here because something is wrong, go straight to **§1 Stop it**.

The calibration harness measures how well-calibrated the Anizai agent's
forecasts are. It takes Polymarket questions with known future answers, sends
them to the existing agent through the existing Firestore flow, waits for the
market to settle, and scores the agent.

It is **strictly additive**: it does not modify the agent, the BFF, the
frontend, or any vault. Everything it writes lives in its own Postgres
database and in two Firestore documents per forecast, both of which it creates
itself.

---

## §1 — Stop it

Three independent levers. **Any one is sufficient.** Use the first that you
can reach.

### Lever 1 — the kill switch (fastest, no cloud access needed)

```bash
export CALIBRATION_ENABLED=false     # PowerShell: $env:CALIBRATION_ENABLED='false'
```

Every command and every task endpoint refuses to run — checked **before** any
Firestore or Postgres call, so nothing is half-done. Verify:

```bash
python -m calibration.cli discover      # expect: exit code 3, "kill switch"
```

### Lever 2 — pause the schedulers (Phase 10D only)

Not applicable yet — no Cloud Scheduler jobs exist. When they do:

```bash
gcloud scheduler jobs pause calibration-discover-hourly  --location us-central1
gcloud scheduler jobs pause calibration-harvest-5min     --location us-central1
gcloud scheduler jobs pause calibration-resolve-hourly   --location us-central1
gcloud scheduler jobs pause calibration-weekly-reforecast --location us-central1
```

### Lever 3 — revoke Firestore access (blunt, last resort)

```bash
gcloud projects remove-iam-policy-binding anizai-ai \
  --account=ron.mintz21@gmail.com \
  --member="serviceAccount:calibration-runner@anizai-pipehub.iam.gserviceaccount.com" \
  --role="roles/datastore.user"
```

> **This command named `anizai-pipeline` until 2026-08-17.** That project is
> dead (expired trial) and its service account with it, so the old command
> removed a binding that no longer existed, **exited 0, and stopped nothing**.
> A safety lever that reports success while doing nothing is worse than one
> that fails loudly — the failure at least sends you to Lever 1.
>
> `--account` is not optional. `kingron79@` has no access to `anizai-ai` at all
> and an IAM call under it returns a permission error, not an empty result.
>
> If access was granted by **impersonation** rather than to the service account
> directly, this is not the lever — revoke the caller's
> `roles/iam.serviceAccountTokenCreator` on the service account instead, in
> `anizai-pipehub`. That cuts token minting immediately and needs no
> `anizai-ai` permissions.

### What stopping does NOT do

- **Forecasts already dispatched keep running.** The agent has them; it will
  finish and write results. They stay `dispatched` in Postgres until someone
  runs `harvest`, or they age into `timed_out` after 120 minutes.
- **Nothing is rolled back.** Stopping prevents new work; it does not undo
  completed work.
- **The agent, the BFF, and the frontend are unaffected.** Calibration is not
  in any of their code paths. If one of them is broken, calibration is not the
  cause — see §6.

---

## §2 — Is it running, and is anything stuck?

```bash
python -m calibration.cli status
```

Read it like this:

| What you see | What it means |
|---|---|
| `dispatched` > 0 and not falling | Forecasts are in flight. Normal for ~1 minute; a concern after 30. |
| `timed_out` climbing | The agent is not answering. Check that it is scaled up (§6). |
| `failed` climbing | The agent is answering with errors. Look at `error_message` on the row. |
| `needs_clarification` climbing | **Not a failure.** The agent asked for clarification; V1 does not answer. A high rate means the question text needs work. |
| everything 0, questions 0 | Nothing has been discovered yet. Run `discover`. |

For the API instead of the CLI:

```bash
curl -H "Authorization: Bearer <id-token>" http://localhost:8000/api/overview
curl http://localhost:8000/healthz          # no auth; reports db reachability
```

---

## §3 — Normal operation

Everything below is idempotent. Running any of them twice is safe.

```bash
# 0. Always start here — validates config, refuses a vault database URL
python -m calibration.cli check-config

# 1. Apply the schema (safe on an existing database)
python -m calibration.cli init-db

# 2. Look at what discovery WOULD add. Writes nothing.
python -m calibration.cli discover --dry-run

# 3. Actually add questions
python -m calibration.cli discover

# 4. Send questions to the agent          <-- the only step that spends money
python -m calibration.cli dispatch --purpose "..." --evidence-caveat "..."

# 5. Collect results (run repeatedly; nothing happens until the agent finishes)
python -m calibration.cli harvest

# 6. Check Polymarket for settled markets and score them
python -m calibration.cli resolve

# 7. Compute and store metrics
python -m calibration.cli compute-metrics
```

### Before every dispatch, record why

`--purpose` and `--evidence-caveat` are stamped onto the run row in the
database. They are not decoration.

A Brier score outlives the conversation that produced it. A run made while the
ingestion pipeline was paused produces a real number that will later be read
as *"how well calibrated the agent is"*, when it actually means *"how well
calibrated the agent is on partial evidence"*. The stamp is the only version
of that caveat which survives.

```bash
python -m calibration.cli dispatch \
  --purpose "pipeline sanity check — not a quality measurement" \
  --evidence-caveat "ingestion paused since 2026-07-23; only NewsAPI/HN/ArXiv ran; momentum vault stale"
```

---

## §4 — Running against live Firestore

By default `dispatch` **refuses** to touch live Firestore. Without
`FIRESTORE_EMULATOR_HOST` set it exits with code 4 and explains why.

To go live you must pass `--allow-live` **and** mean it: every dispatched
question is a real agent session that calls OpenAI and costs money.

### Preflight — all four must be true

1. **The agent is scaled up.** It is deliberately held at `replicas: 0` and
   brought up per run. See §6.
2. **A dispatch limit is set.** `CALIBRATION_MAX_FORECASTS_PER_RUN` — start at
   5, not 30.
3. **You know the state of the vaults.** If ingestion is paused, the agent
   forecasts on stale evidence. Record it in `--evidence-caveat`.
4. **You know how to stop.** §1.

```bash
export CALIBRATION_MAX_FORECASTS_PER_RUN=5
python -m calibration.cli dispatch --allow-live --question-id <one-id> \
  --purpose "..." --evidence-caveat "..."
```

**Send one question first.** Wait for it to complete. Only then send the rest.

---

## §5 — Debugging a stuck forecast

```bash
python -m calibration.cli list-questions --status open
```

Take the `sessionId` from the forecast row (it starts with `cal_`), then:

| Where to look | What it tells you |
|---|---|
| `sessions/{sessionId}` in Firestore | `status` — `queued` means nobody claimed it; `running` means the agent has it; `failed` carries `errorMessage`. |
| `sessionResults/{sessionId}` | Exists only once the agent finished successfully. |
| `sessions/{sessionId}/evidence` | What evidence the agent actually used. Empty is legal, not an error. |
| agent worker logs | `kubectl logs -n anizai deploy/agent-worker --tail 200` |

### Common states

**Stuck at `queued`** — nothing claimed it. Almost always the agent is at
`replicas: 0`. See §6.

**Stuck at `running` for a long time** — the agent has it and is slow. The
harvester marks it `timed_out` after `CALIBRATION_DISPATCH_TIMEOUT_MIN`
(default 120). Raise that value rather than re-dispatching; a re-dispatch
spends tokens on a question already in flight.

**`needs_clarification`** — working as designed. The agent asked a question
back. V1 does not answer, so the row is terminal and excluded from scoring.
Not a bug and not a failure.

**A forecast row with no session document** — a dispatch died between its two
Firestore writes. Benign: the row is only inserted after both succeed, so this
leaves an orphan session with no row, invisible to every metric.

---

## §6 — The agent is deliberately off

The agent runs at **`replicas: 0`** by design and is brought up per run. This
is not a fault, and it is not something to "fix" permanently.

```bash
kubectl get deploy agent-worker -n anizai          # 0/0 = off, 1/1 = up
kubectl scale deploy agent-worker -n anizai --replicas=1
# ... run the calibration cycle ...
kubectl scale deploy agent-worker -n anizai --replicas=0
```

> **Do not `kubectl apply` the agent manifest to bring it up.** Several
> workloads are held at `replicas: 0` live while their committed manifests
> still declare `replicas: 1` — `flink-jobmanager`, `polymarket`, `telegram`.
> An `apply` would start all of them. Use `kubectl scale`.

---

## §7 — Inspecting the database

```bash
psql "$CALIBRATION_DATABASE_URL"
```

```sql
-- what ran, and what it was for
SELECT triggered_at, run_type, triggered_by,
       run_metadata->>'purpose'         AS purpose,
       run_metadata->>'evidence_caveat' AS caveat
FROM calibration_runs ORDER BY triggered_at DESC LIMIT 10;

-- forecasts by state
SELECT status, COUNT(*) FROM calibration_forecasts GROUP BY status;

-- anything in flight longer than an hour
SELECT session_id, forecast_dispatched_at, NOW() - forecast_dispatched_at AS age
FROM calibration_forecasts
WHERE status = 'dispatched' AND forecast_dispatched_at < NOW() - INTERVAL '1 hour';

-- scored forecasts, worst first
SELECT q.question_text, f.final_probability, f.brier_score, r.outcome
FROM calibration_forecasts f
JOIN calibration_questions q   ON q.id = f.question_id
JOIN calibration_resolutions r ON r.question_id = f.question_id
WHERE f.brier_score IS NOT NULL
ORDER BY f.brier_score DESC LIMIT 20;
```

---

## §8 — Cleaning up after a failed live run

Calibration sessions are identifiable two ways, and both hold for every
document it ever writes:

- `userId == "calibration-runner"`
- `metadata.calibration.enabled == true`

To remove calibration sessions from Firestore, scope on one of those. **Never
delete by collection** — `sessions` and `forecastQueries` hold real users'
data.

Orphan sessions — created when a dispatch died between its two writes — look
like: `userId == "calibration-runner"` AND `status == "queued"` AND older than
24 hours AND no matching row in `calibration_forecasts`. They are harmless
(no worker will claim them, no metric sees them) but should be swept
periodically.

To wipe the calibration **database** and start over — this touches no
Firestore and no vault:

```sql
TRUNCATE calibration_forecasts, calibration_resolutions,
         calibration_metrics_snapshots, calibration_questions,
         calibration_runs RESTART IDENTITY CASCADE;
```

---

## §9 — Configuration

| Variable | Default | What it does |
|---|---|---|
| `CALIBRATION_DATABASE_URL` | local `anizai_calibration` | The calibration Postgres. **Refuses to connect to a database named `anizai`** — that is the vault DB. |
| `CALIBRATION_ENABLED` | `true` | The kill switch. §1. |
| `CALIBRATION_MAX_OPEN_QUESTIONS` | `30` | Discovery will not exceed this. |
| `CALIBRATION_MAX_FORECASTS_PER_RUN` | `30` | Dispatch truncates past this **and logs the truncation**. |
| `CALIBRATION_DISPATCH_TIMEOUT_MIN` | `120` | How long a forecast may stay in flight before `timed_out`. |
| `CALIBRATION_TARGET_COUNT_7D/_14D/_30_45D` | `10/10/8` | Per-cohort targets. |
| `CALIBRATION_LIQUIDITY_MIN_7_14D_USD` | `50000` | Volume floor. Long-horizon uses `_30_45D` (`25000`) — there are fewer liquid long-dated markets. |
| `FIRESTORE_EMULATOR_HOST` | unset | When set, everything targets the emulator. When unset, `dispatch` demands `--allow-live`. |
| `FIREBASE_AUTH_OPERATOR_EMAILS` | unset | Dashboard allowlist, comma-separated. **Empty denies everyone** — a missing secret must not open the dashboard. |

`check-config` prints all of it and validates it.

---

## §10 — Running the tests

```bash
cd calibration
pytest
```

That is the whole command. `pytest.ini` in this directory sets `pythonpath=src`
and `testpaths=tests`, so the suite needs no installation step and no
environment fiddling.

Run it **from `calibration/`**. The repository also holds
`data-pipeline/tests/conftest.py`, which imports the pipeline's database layer;
pulling that in would make this suite require the pipeline's dependencies, and
running without them is a property worth keeping. From the repository root the
cut has to be passed explicitly, because an ini file cannot set it:

```bash
pytest calibration --confcutdir=calibration
```

Tests needing a backend skip cleanly when it is absent. To run them all:

```bash
docker run -d --name anizai-calibration-test \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=anizai_calibration \
  -p 55432:5432 postgres:16-alpine

cd server/firebase && npx firebase-tools emulators:start --only firestore --project anizai-ai

export CALIBRATION_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/anizai_calibration
export FIRESTORE_EMULATOR_HOST=localhost:8080
```

Dashboard:

```bash
cd calibration/dashboard
npm install
npm test
npm run build
```

To run it against a live API instead of the test suite, start the operator API
first and then Vite **bound explicitly**:

```bash
# terminal 1 — the API
CALIBRATION_DATABASE_URL=... uvicorn calibration.server:app --host 127.0.0.1 --port 8000

# terminal 2 — the UI
node node_modules/vite/bin/vite.js --host 0.0.0.0
```

`--host` is not cosmetic. Vite's default binds `::1` only; a browser resolving
`localhost` to `127.0.0.1` then gets a connection refusal that looks like the
dev server never started. The proxy in `vite.config.ts` already points `/api`
at `127.0.0.1:8000` for the mirror image of this problem.

---

## §11 — What this system must never do

If you find it doing any of these, **stop it (§1) and treat it as a defect**:

1. Modify anything under `data-pipeline/agent/`, `server/`, or `client/`
2. Write to any vault table, or to `sessionResults`, or to a session
   subcollection
3. Read, modify, or delete a session it did not create
4. Create a Firestore document without `userId == "calibration-runner"` and
   the `metadata.calibration` block
5. Connect to a Postgres database named `anizai`

Points 1–5 are enforced by `tests/test_calibration/test_isolation.py`, which
parses the source and fails on violations. If that file is ever deleted or
weakened, these guarantees revert to being a matter of care.
