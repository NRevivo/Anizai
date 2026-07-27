# Phase 7D — Cloud verification window · Deployment Log

> Domain: A — Pipeline · Type: Deployment record (T11)
> Plan: `docs/A_pipeline/archive_plans/phase7d_enrichment_gating.md` §8 (rewritten 2026-07-27)
> Window RUN_ID: `phase7d-verify-20260727` · Deliverables dir: this folder
> This log is the running record of T11. `:t_start` is recorded at §8.3 step 9.

---

## Pre-T11 — commit of T1–T10

- Commit `6ebd753` `feat(A): Phase 7D enrichment gating + social-path reject coverage (T1-T10)`.
  Author & committer `ron45700 <ron.mintz21@gmail.com>`, no Claude attribution. 20 files.
- Confirmed before commit: `requirements.lock` untouched (§7 rule 6); `init.sql` updated
  alongside migration 004 (additive only); local Docker stack down (§7.1 rule 4).

## Pre-T11 — cloud state confirmed clean

- `anizai-flink:1.19.1-7d` absent from Artifact Registry (before build).
- Both Flink manifests (JM + TM) still declare `1.19.1-7b5i`.
- `main-pool` at 0 nodes (`kubectl get nodes` → No resources found).

---

## §8.3 step 1 — Build & push `anizai-flink:1.19.1-7d` (P1) — DONE 2026-07-27

| Item | Value |
|---|---|
| Build | `docker build -f infrastructure/Dockerfile.flink -t …/anizai-flink:1.19.1-7d .` (context `data-pipeline/`) |
| Build time | **76 s**, rc=0 |
| Push time | **17 s**, rc=0 |
| AR digest | `sha256:9a73a780ea7eae000a39cd3c20d9b6f94b223559e92feec41766c4faebced303` (== local build digest → no stale-layer risk) |
| AR createTime | 2026-07-27 08:52 UTC |
| Untouched | `-7b5i` (`sha256:39ffe4b8…`), `:latest`, `:1.19.1` — new tag only, per P1 |

**Reference digest for §8.3 step-6/7 running-image check (addition C):** the deployed
JM + TM pods must report imageID `…@sha256:9a73a780…`. Anything else = stale pull.

### Note — LAYER 6 cache-missed and re-resolved range-pinned deps (verified low-risk)

The `COPY requirements.txt` layer did not match a cached entry (build cache 30%
reclaimable — the `-7b5i` layer was GC'd), so LAYER 6 (`pip install -r requirements.txt`)
re-ran and re-resolved the range-pinned dependencies from PyPI. `Dockerfile.flink`
installs from `requirements.txt`, not the lock, so every image rebuild does this
(`-7b5i` and `-p95` were built the same way). `requirements.lock` was **not** touched and
`pip freeze` was **not** run (§7 rule 6 / KG-A-11 respected).

Parity was verified by diffing `pip freeze` of `-7b5i` (deployed) vs `-7d`:

- **All load-bearing libs identical:** `apache-flink==1.19.1`, `apache-beam==2.48.0`,
  `openai==1.109.1`, `httpx==0.27.2`, `psycopg2-binary==2.9.9`, `numpy==1.24.4`,
  `pgvector==0.3.2`, `pydantic==2.13.4`, **`protobuf==6.33.6` (both)**.
- The `apache-beam 2.48.0 requires protobuf<4.24.0 … you have 6.33.6` pip warning is
  therefore **pre-existing in the production `-7b5i` image**, not introduced by this
  rebuild — `-7b5i` ran the full day-run with it.
- Drift is confined to 22 minor transitive patch bumps the Gold gating / enrichment /
  embedding / DB paths never exercise (PyJWT, cryptography 48→49, grpcio 1.80→1.83,
  SQLAlchemy 2.0.49→2.0.51, google-* SDKs, tqdm, xxhash, …) plus one addition
  (`prometheus_client==0.26.0`; Flink metrics come via the Java reporter, LAYER 4B).

Verdict: measurement runtime effectively unchanged vs the deployed image. Full diff +
both digests + both freezes: **`image_freeze_diff.txt`** in this folder (moved out of
scratchpad — files, not scratch; it is the only evidence `-7d ≡ -7b5i` on the load-bearing
set if the drift is ever questioned during the multi-day run).

**Why this is an easy call, not a close one (Ron, 2026-07-27):** every output of this
window (C1–C9) is a **count or a ratio** — a correctness verification — not a timing or
cost figure. Dependency drift would move a cost/latency baseline; it cannot move a count.
The same drift therefore **does** matter for the multi-day run, which IS a cost baseline —
flagged in the T12 carry-over below.

**One watch item (Ron, 2026-07-27):** grpcio 1.80.0→1.83.0 is not purely a Google-SDK
transitive — gRPC is the transport Beam uses between the JVM operator and the Python UDF
worker, i.e. on the runtime path of every Python operator (the KG-A-10 component). Not a
blocker (minor bump; the historically dangerous lib, `protobuf 6.33.6`, is identical), but
it changes what to watch: if it bites it bites **loudly** at step 7 (jobs reach RUNNING),
step 8 (startup lines appear), or Gate B — never silently. Treat a Beam/gRPC error at any
of those three points as dependency-suspicion-first, not a 7D code bug.

**Local Docker:** only `docker build` + `docker push` + two ephemeral `--rm` pip-freeze
containers were run; the local compose stack was never up (§7.1 rule 4 / §8.1 step 4 hold).

---

## T12 carry-over items (record, do not fix this sprint)

- **New proposed gap — KG-A-15 (Flink image builds are non-reproducible by construction).**
  `Dockerfile.flink` LAYER 6 installs from `requirements.txt` (range-pinned), not
  `requirements.lock`, so every rebuild re-resolves transitive deps from PyPI — this is why
  `-7d` drifted from `-7b5i` at all (22 transitive bumps; load-bearing set identical). It
  recurs on every future rebuild, **including the one before the multi-day cost baseline**,
  where drift is no longer immaterial. **Adjacent to but distinct from KG-A-11:** KG-A-11 is
  orphans left *inside* the lock; this is the image *bypassing* the lock entirely. Priority:
  Low for 7D. Raise in `pipeline_sprints.md §4` at T12; do not fix here. (CLAUDE.md §4.4
  states "Docker builds install from the lock file" — `Dockerfile.flink` does not, so either
  the Dockerfile or that policy line is out of date; note the discrepancy at T12.)

---

## §8.3 step 2 — Bring-up (PIPELINE profile, taps closed) — DONE 2026-07-27

- Context `gke_anizai-pipeline_us-central1-a_anizai-cluster`, project `anizai-pipeline`, 0 nodes at start.
- **Pre-session desired replicas (KG-C-10):** all five held workloads found **already at 0**
  (`flink-jobmanager`, `flink-taskmanager`, `telegram`, `polymarket`, `agent-worker`) — live
  drifted to 0 while manifests declare 1, as expected (§5 trap 4). Re-scaled all to 0
  (idempotent), gate re-read confirmed all 0.
- Resized `main-pool` → 1 node (42 s). `postgres-0`, `kafka-0`, `airflow-*`, `trigger-consumer`,
  monitoring all Running/Ready (~104 s). **No pod exists for the five held workloads** — hold took.

## §8.3 step 3 — Migration 004 on cloud Postgres — DONE 2026-07-27

- PRE: `filter_rejects` had no `canonical_event_id`, no `idx_filter_rejects_cei` (cloud was pre-migration).
- Applied via `kubectl exec -i postgres-0 -- psql -U anizai -d anizai -v ON_ERROR_STOP=1`.
  `ALTER TABLE` + `CREATE INDEX`; RAISE NOTICE confirmed column=1, index present.
- POST: `canonical_event_id` (text, nullable) ✓; `idx_filter_rejects_cei` ✓.

## §8.3 steps 4+5 — C0 baseline + queue measurement (measured ONCE, addition A) — DONE 2026-07-27

Full data → `baseline_pre_window.csv`. Headlines:

- **Kafka:** Bronze retained total **48,712**, dominated by `polymarket` 34,819 (NON-LLM:
  structured_metrics + flag-off comments). **Silver retained = 0** (aged out) → gold_job has no
  backlog replay. DLQ end offset **7,142** (C9 baseline). LLM-relevant replay exposure = HN 3,900
  (the expensive re-key + reject-pollution half) + global_news 8,223 (mostly gate-skipped, already archived).
- **C0-3 social_vault HN (day-run window):** 127 rows / 59 distinct story_id = **2.153 rows/story**
  → HN was duplicate-enriched ~2.15× → **retro-validates D1 option (a)**. (Feeds T12 item 8.)
- **C0-4 social_vectors (window):** 29 rows (all hackernews) — first-ever export, closes dayrun §4.1.
- **C0-5 knowledge_vectors:** 27,459 total, 27,459 distinct signal_id (PK — trivially equal, NOT the
  debt), 5,154 distinct canonical_event_id → **22,305 duplicate vectors (81%)** = large KG-A-8 debt;
  last recorded 9,202 predates day-run → **strongly supports the §8.4.3 reset** before the multi-day
  run. (Feeds T12 item 9.)
- **RPD (addition B):** 0 calls today (UTC), $0, last event 2026-07-23 → **full 10k/day Tier-1 cap
  free**, headroom not tight. Dollar credit balance not queryable here (Ron's OpenAI dashboard).

## §8.2 Gate A — Ron gave explicit go 2026-07-27. Truncation DONE.

- Truncated the 7 `ingest.bronze.*` topics with retained>0 (arxiv, fred, hackernews, newsapi,
  openweather, polymarket, telegram) via `kafka-delete-records` (offset JSON = measured per-partition
  end offsets → `truncation_offsets.json`). All 21 partitions: low_watermark now == end, **retained=0**.
  Silver already 0 (nothing to do). **DLQ untouched at 7,142.**
- Transfer note: `kubectl exec -i` stdin→`sh -c` redirect is broken in this Docker-Desktop/Windows
  setup; wrote the JSON into the pod via a base64 **argument** (`echo <b64> | base64 -d > file`) instead.

### C0 corrections (Ron, 2026-07-27) — folded into `baseline_pre_window.csv`

- **CORR2 — HN enrichment duplication = 12.41×, not 2.15×.** The 2.15× (social_vault rows/story) is
  *archival* duplication, a lower bound. The D1 figure is `gold_consensus` calls ÷ distinct story:
  **732 / 59 = 12.41×** (gold_embed also 732, 1:1). Cross-check: day-run pulled ~3,600 HN; high-signal
  = 732 = consensus exactly (pre-7D every high-signal delivery enriched); 3,600−732 = 2,868 low-signal
  rejected (79.7%) = KG-A-12/§0 corpus. Denominator 59 confirmed sound. **D1 option (a) strongly validated.**
- **CORR1 — vector debt = 24,700 excess (~90%), not 22,305/81%.** Authoritative article denominator is
  `knowledge_vault` (UNIQUE `document_hash`, F2) = **2,759 articles**. 27,459 vectors / 2,759 = **9.95×**;
  excess = 24,700. My Gate A `canonical_event_id`-based figure was wrong — `distinct canonical` (5,154) is
  finer than article (partly per-delivery via bronze_ref), so it under-measured the debt. Even stronger
  support for the §8.4.3 reset before the multi-day run. (Feeds T12 item 9.)
- **Silver-replay note (Ron):** Silver retained = 0 means `gold_job` had **no replay exposure at all**;
  the entire replay exposure was via Silver *regenerating from Bronze* — which the Bronze truncation removes.
- `social_vectors` = 29 (first-ever export) **closes `dayrun_analysis.md` §4.1**, open since the day-run.

## §8.3 step 6 — manifests updated + applied — DONE 2026-07-27

- Both Flink manifests edited (uncommitted, per Ron's git plan): image → `1.19.1-7d`; four env vars in
  JM+TM identically (`REJECT_CAPTURE_ENABLED=true`, `RUN_ID=phase7d-verify-20260727`,
  `ENRICHMENT_DEDUP_GATE_ENABLED=true`, `LOG_INFO_SAMPLE_RATE=1.0`). `kubectl apply` → `replicas:1`
  (addition E — apply is what brings Flink up; not scaled separately).
- **Addition C:** running JM+TM imageID = `sha256:9a73a780…` == AR digest. No stale pull.
- Manifest-comment cleanup (the stale "false post-run" / "T8 replaces" comments) deferred to the
  teardown commit (§8.4.4 step 3), where the durable set is committed.

## §8.3 step 7 — cancel + resubmit — DONE 2026-07-27

- HA recovered 2 old `-7b5i` jobs (`anizai-silver-polymarket`, `anizai-gold-all-sources`); cancelled both.
- Resubmitted `silver_job.py` (jid `7595f536…`) + `gold_job.py` (jid `ef1ea93a…`). Both **RUNNING**,
  TM restarts=0, no RESTARTING loop. The grpcio/Beam watch-item (addition D) did **not** bite — compiled
  and submitted clean. Gold Kafka consumers seek `earliest`==end on the empty Silver topics → no replay
  (confirms the truncation). 122 "Exception" matches are benign Kafka startup retries
  (CoordinatorNotAvailable / RetriableCommitFailed); **0 ERROR-level lines, 0 Python traceback.** DLQ flat 7142.

## §8.3 step 8 — STARTUP-LINE GATE — plan-vs-reality conflict, ESCALATED to Ron

- The six `[gold/flink]` startup lines are **absent**: 0 `[gold/flink]`, 0 `[gold/dedup]`, **0 Beam bundles
  processed**. Only Flink's JVM config logging is present.
- **Root cause:** in this PyFlink/Beam build the operator `open()` logging is **data-triggered** (fires when
  the first Beam bundle starts), not at job-start. The system is silent by design (Bronze truncated, Silver
  empty, no traffic), so no bundle has run → `open()` has not executed → no lines. This contradicts §8.3
  step-8's "operators log their flag state at job start, on a silent system" assumption. The plan's
  code-reading was right (the code DOES log in `open()`); the operational assumption about **when** `open()`
  runs is wrong for this build. → **T12 discrepancy note.**
- **Gate intent verified by other means instead:**
  - all four env vars present in the running TM (`printenv`) AND in the Python worker's Beam env payload;
  - `sniper_reference_vector.npy` present in the image (6272 bytes) → no `FileNotFoundError` at `open()`;
  - both jobs RUNNING, restart-free, no ERROR-level/traceback; DLQ flat.
- **Unverifiable until data flows** (i.e. until T0): the operators actually executing `open()` with the right
  flags — the `[gold/dedup]` skip lines, `threshold=0.35`, sniper shape. **This is exactly what the T0+5
  abort checkpoint (§8.3 step 10) already checks.**
- **Ron's decision (2026-07-27): fold step 8 into the T0+5 abort checkpoint (step 10), after closing
  two gaps via zero-data / zero-spend in-container checks. Plan §8.3 steps 8+10 corrected. No nudge
  (a single-source nudge would self-seed C2 and blind the §6 first-occurrence assertion).**
- **Step-8 pre-T0 checks (a/b/c/d) — ALL PASS:**
  - (a) raw env in TM: gate=`true`, reject=`true`, RUN_ID=`phase7d-verify-20260727`, sample_rate=`1.0`.
  - (b) **parsed `config.settings` in-container** (closes the raw-vs-parsed gap): `ENRICHMENT_DEDUP_GATE_ENABLED=True`,
    `REJECT_CAPTURE_ENABLED=True`, `RUN_ID='phase7d-verify-20260727'`, `GOLD_SEMANTIC_RESCUE_THRESHOLD=0.35`
    — the exact four values the six lines echo; `"true"`→`True` bool parse confirmed (no truthy-string trap).
  - (c) `numpy.load(sniper_reference_vector.npy)` → `shape=(1536,)`, `float32` — loads, no `open()` hard-fail.
  - (d) both fresh jobs RUNNING, old CANCELED, DLQ flat 7142, no ERROR/traceback.
- **The six log lines now verify at T0+5 (step 10), strengthened:** GlobalNews lines REQUIRED and must
  read correctly; social lines held open until first HN bundle; expect each ≥2× (parallelism=2) — a SHORT
  count, not duplicates, is the warning.
## §8.3 step 9 — T0 (taps open) — DONE 2026-07-27

- **`:t_start = 2026-07-27T10:00:24Z`** — all close-pack queries bind on this (timestamp, never RUN_ID).
- Unpaused `newsapi_high_frequency` + `hackernews_high_frequency` (is_paused→False); scaled `telegram` +
  `polymarket` → 1 (Running/Ready); manually triggered both DAGs (manual runs 10:00:29 / 10:00:34, running).
- Airflow CLI env: replicated the wrapper's `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN` (password from CSI file,
  never printed) — a fresh `kubectl exec` lacks it (documented in the scheduler manifest's liveness note).
- **Bonus duplicate pair:** unpausing also fired each DAG's last scheduled interval (~10:00:17, catchup=False),
  so newsapi/hackernews each got scheduled + manual near-identical fetches — extra C2/C3 grist. Gold events
  land after `:t_start` (enrichment lag), so window coverage is intact.
- Window = 90 min from `:t_start` → close ~11:30Z (P6).

## §8.3 step 10 — T0+5 ABORT CHECKPOINT — FAILED (TM OOMKilled). T0 VOID pending Ron's fix decision.

- **TaskManager `OOMKilled` (exit 137), CrashLoopBackOff, restart count 5.** Both jobs RESTARTING.
  Last cycle: started 10:05:23Z, killed 10:06:01Z (~38s under load). Container mem limit **2560Mi**.
- **Not a 7D logic bug, not the grpcio/Beam dependency** (no traceback) — a **memory-capacity imbalance**:
  `taskmanager.memory.process.size=2048m` (Flink JVM) + the Beam **Python worker processes** (~166MB each;
  ~14 were "obtained" — ≈2.3GB) run OUTSIDE the JVM in the same 2560Mi container → limit is ~2GB short.
  Idle at step 7 (no data) it fit; when data hit at T0 the workers materialized → OOM.
- **Zero damage:** **0 `llm_cost_events`, 0 `filter_rejects` since T0** (crashes before enrichment), DLQ flat 7142.
  CrashLoopBackOff is self-throttling (backoff → 5min) so no spend runaway.
- Six startup lines never appeared — TM crashed before any bundle completed `open()`. Consistent with OOM.
- **Node headroom is huge:** e2-standard-8, 6677Mi/23% used — ~25GB free. Raising the TM limit is trivial to fit.
- **Same config ran the day-run on `-7b5i`** (resources unchanged by 7D — I edited only image + 4 env vars),
  so this is either a pre-existing marginal condition tipped by the simultaneous all-source burst at T0
  (scheduled+manual newsapi/hackernews + telegram/polymarket scale-up) or a slightly heavier `-7d`. Either
  way it would recur on the multi-day run and may relate to KG-A-9's "restart replay loops."
- **STOPPED per Ron's stop-and-ask directive at this checkpoint. Changed nothing.**

### Ron's fix decision (2026-07-27) + notes

- **Ron's note:** the synchronised "open every tap at once" T0 is his design and is what surfaced a **latent
  capacity fault** as an immediate kill — a staggered ramp would have hidden it, so finding it now is a good outcome.
- **Fix = raise TM container memory only** (JVM `process.size` stays 2048m): **limit 2560Mi → 6Gi, request 2048Mi → 4Gi**.
  6Gi so arxiv's extra Silver branch (step 11) + the full producer set (multi-day) fit without a second mid-window edit;
  request 4Gi so the scheduler reserves realistically on a node shared with postgres/kafka/prometheus (eviction risk).
  Parallelism 2→1 rejected (set in code, `-p 1` wouldn't override; changes the config being measured).
- **AMENDMENT — polymarket dropped from the window** (held at 0, brought up at teardown with the agent). Highest-volume
  producer, feeds structured_metrics (no LLM), 7D measures nothing through it; removing it drops the Silver polymarket
  branch + Gold metrics operator (~4 workers, ~660MB) at zero measurement cost. P5's "variable if C9 looks odd" flag
  cashed in. **telegram stays up — the sprint's control source.**
- **Crash-window archival check (before restart): CLEAN — 0 `knowledge_vault` + 0 `social_vault` rows since void T0.**
  No stranded KG-A-14 case.

### TEARDOWN CHECKLIST (§8.4.4 step 3) — the resources edit MUST join the durable commit

The TM memory edit (**limit 6Gi, request 4Gi**) is a **real pre-existing capacity fix** — it joins the DURABLE set
committed at teardown alongside the `-7d` image + the two stays-true flags (`REJECT_CAPTURE_ENABLED=true`,
`ENRICHMENT_DEDUP_GATE_ENABLED=true`), with `RUN_ID` + `LOG_INFO_SAMPLE_RATE` reverted. **Do NOT let the resources
edit be reverted with the window-specific values.** (Ron recorded this in plan §8.5.)

### T12 open question (record next to KG-A-15, the Dockerfile gap)

"Did the `-7d` dependency bump raise the Python-worker footprint, or was 2560Mi always marginal and the synchronised
T0 merely the trigger?" Resolving it needs a comparison run under the same burst; the action (raise memory) is identical
either way, so recorded and deferred, not investigated now.

## §8.3 step 10c — Prometheus evidence (Ron-directed) → the real suspect

- **Step 1 (RS templates):** the 6Gi DID apply — rev-14 RS (`785fffcbcb`, created 10:56:34Z) template =
  6Gi/4Gi served the 2nd OOM pod; current spec 6Gi/4Gi. "Never applied" ruled out.
- **Step 2 (Prometheus day-run vs today):** **day-run window is CLEAN** — TM `up` 100%, 0 state changes,
  registered-TM min=max=1, **0 job restarts**, 2 failed checkpoints over 24h. Today: `up` 21%, 40 state
  changes, dropped to 0, tens of restarts. **So 2560Mi genuinely ran all 9 sources for 24h — NOT KG-A-9,
  NOT a capacity floor. Cause is something changed today.**
- **Signature (Ron):** 2.4× the limit moved time-to-kill only 38s→34s → consumption grows at a RATE that
  scales with throughput, not a fixed footprint. Only one delta behaves that way: **`LOG_INFO_SAMPLE_RATE=1.0`**.
  (Initial mechanism guess was 100× log records over the Beam channel — **NOT established**, see the unresolved
  note below; the empirical differentiator stands, the explanation does not.)

## §8.3 step 10d — FIX ATTEMPT 2: revert to day-run config (Ron-directed) — IN PROGRESS

- **Step 3 applied + verified:** both manifests reverted — `LOG_INFO_SAMPLE_RATE` REMOVED (JM+TM), TM
  resources back to **2560Mi/2Gi**; KEPT `-7d` image + `REJECT_CAPTURE=true` + `ENRICHMENT_DEDUP_GATE=true` +
  `RUN_ID`. New TM pod `xht7z` verified (limit 2560Mi, LOG_INFO_SAMPLE_RATE absent). JM apply hit the Flink-HA
  rolling-update deadlock (RollingUpdate + standby 503); broke it with a JM scale 0→1 (no jobs running). New
  JM `k5d9m` Ready.
- **Step 4:** producers stay off, DAGs paused, queue NOT truncated (accumulated Bronze IS the window — it has
  real newsapi/hackernews duplicates). Resubmitted silver `03ad8689…` + gold `785ea9a1…`.
  **`:t_start (attempt 2) = 2026-07-27T12:38:53Z`.** 60s heap watch running (JVM heap flat+low while dying →
  Python-side (not JVM); climbing → JVM problem). NOTE: "logging-channel" mechanism NOT established — see below.

### RESULT — attempt 2 SURVIVED; submission+5 checkpoint FULL PASS; queue DRAINED & measured

`:t_start (attempt 2) = 12:38:53Z`. Queue NOT re-truncated (accumulated Bronze WAS the window — held the real
newsapi/HN duplicates C2/C3 needed).

- **TM survived** — restarts=0 through the whole window; heap flat sawtooth ~170–536MB, never climbing → OOM
  was Python-side, not JVM. Both jobs RUNNING, DLQ flat 7142, ERROR=0. The revert fixed it.
- **Six `[gold/flink]` startup lines** all present, correct, ≥2× (sniper ×4): dedup gate + reject capture
  ENABLED (`run_id='phase7d-verify-20260727'`), threshold 0.35, sniper (1536,), social gate + social reject
  ENABLED. Step-8 verification satisfied at submission+5 as the corrected plan intended.
- **C1 (gate fires):** 56 `[gold/dedup]` skips — news 46, HN 10. **Unsampled** (INFO passes full on the Flink
  UDF path despite the 0.01 default — 14/14 startup lines + counts match the ~200-msg backlog), so this is the
  true count, not a 1% sample.
- **C2 (newsapi, calls per DISTINCT item):** 62 distinct enriched once; 46 duplicate deliveries skipped →
  without gate 108 `gold_enrich`, with gate 62 → **1.74→1.00, a 43% cut** (day-run was 2.11). The pre-gate
  1.74 < the day-run's 2.11 is NOT the gate doing less: duplication accumulates with elapsed time (24h of
  repeat pulses/article then vs ~2 here), and the gate's effect scales with how much duplication exists to remove.
- **C3 (wasted enrichment):** **0** — every `gold_enrich` has a matching `knowledge_vault.canonical_event_id`.
- **C5 (HN rejects — KG-A-12 CLOSED):** 288 rows / **50 distinct URLs** / **0 NULL** instance keys. 288/50 =
  5.8 rows/story — the per-instance pattern C5's guard predicted for a multi-pulse queue.
- **C6 (instance key):** 0 NULL `canonical_event_id`, both sources.
- **C7 (KG-A-8 proof):** `knowledge_vectors` 62 = `knowledge_vault` 62 (1:1) — deterministic `signal_id`, no
  second vector on re-delivery.
- **Spend:** 535 calls, $0.0287.
- **Caveats:** (1) **social path barely exercised** — 2 high-signal HN stories; T4 **fired** (10 skips across
  2) but **n=2** → fired, not "verified". (2) **HN reject rate this slice ≈96%** (50 of 52 distinct) vs
  day-run 79.7% — single slice n=52, record don't chase (bears on §0 corpus estimates).
- **OOM cause (defensible):** `LOG_INFO_SAMPLE_RATE` was the differentiating variable across attempts at
  constant memory; flat JVM heap places growth Python-side. Not sole-cause on one trial. (Ron rewrote §8.5.)
- **Drain:** kv_vault=62 / llm_events=535 / filter_rejects=407 / silver_gn_end=20497 flat over 6 samples
  (~2 min) → newsapi/HN phase closed. Awaiting Ron's arxiv go.

### T12 / TEARDOWN — JM HA rolling-update deadlock (record, do NOT act mid-run)

A JM manifest `apply` stalls: old leader won't release the HA lock, standby JM returns 503 (RollingUpdate).
Cleared by scale 0→1. **Recurs on every future JM edit.** One-line fix — **`strategy.type: Recreate` on the
JobManager Deployment** — belongs in the teardown commit. The 0→1 workaround is **only safe with no jobs running.**

### C4 — arxiv (highest-yield gate demonstration) — PASS; TM survived the burst

arxiv trigger 13:20:58Z; drained ~13:28. **TM restarts=0 through the ~2,800-msg Bronze burst** (volume non-event
at 2560Mi + logging reverted, as the day-run showed). Skip counts unsampled → true.

Four measures: arxiv Silver deliveries ≈ **2,828** (1,850 high-signal + 978 low-signal; Bronze produced 2,800,
consistent); distinct arxiv papers = **570** (102 high-signal + 468 low-signal); `gold_enrich` = **102**;
`[gold/dedup]` arxiv skips = **1,748** (1,794 total − 46 newsapi baseline).

**Headline: `gold_enrich` ÷ distinct high-signal = 102/102 = 1.0**; skips (1,748) ≈ high-signal deliveries
(1,850) − enrichments (102). **Without the gate arxiv pays 1,850 `gold_enrich`; with it, 102 → a 94.5% cut,
18.1 → 1.0 calls per distinct high-signal paper.**
- C7 (arxiv, KG-A-8): `knowledge_vectors` 102 = `knowledge_vault` 102 = `gold_enrich` 102 → 1:1:1.
- Rejects: 978 rows / 468 distinct / **0 NULL** instance keys. Spend 1,182 calls (102 enrich + 102 embed + 978 rescue_embed).
- **CORRECTION — arxiv ran TWICE** (verified): my `manual__13:20:47` + the `scheduled__2026-07-26T07:00` **run for
  the latest interval, created on unpause** (expected under `catchup=False` — NOT backfill; same as newsapi/HN at
  T0). So **18.1× = two runs combined; per run ≈ 9×**, which IS
  comparable to the day-run's single-run 8.4× — do NOT report 18.1 as comparable to 8.4. The gate absorbed the
  second (catchup) run — same ~102 papers, all already archived → **all skipped, 0 new enrich** — so the double
  burst *strengthens* the result. `gold_enrich`=102=distinct still holds; 94.5% cut of the combined 1,850→102 stands.
- **468 distinct rejected = measured today** (rejects span 13:20:48→13:27:45, bounded on t_start); the match with
  the day-run's 468 is **structural** (fixed categories + max_results ⇒ ~468 distinct/run), not inherited.
- **7B.5 finding (record, not 7D's territory):** high-signal arxiv papers arrive **18.1×** each (1,850/102),
  low-signal only **2.09×** (978/468) — duplication correlates strongly with the sniper PASSING. Plausible reading:
  cross-listed interdisciplinary AI papers (appearing under several category queries) are genuinely the relevant
  ones, so **cross-listing is itself a relevance signal.** A real observation from this run for 7B.5.

### T12 — NEW Known Gap: social-path signal-flipping lockout (follows from D1a; NOT a 7D fix)

Social archival happens BEFORE the `is_high_signal` check. A low-signal HN story on its FIRST delivery is archived
+ reject-captured, then returns. On a LATER delivery, if it is now high-signal, `exists_by_content_hash` is already
true → T4 skips it → it is **never enriched**. The old comment-inclusive hash prevented this (fresh hash per
delivery). So D1a may in practice mean **"enriched only if high-signal on FIRST delivery"** — a **coverage**
reduction (changes WHICH stories are enriched), not just duplication. **This slice: NOT demonstrated (disjoint)** —
the 2 enriched stories (49057248, 49057574) have 0 overlap with the 288 rejects. Record for T12; it also reframes
C2's HN row as "the gate can change which stories get enriched."

### T12 — first D2 counter-example (7B.5 hand-off): HN max rescue_cosine 0.3523 > 0.35 promote threshold.
One HN story (1 of 288) above the threshold D2 assumed nothing exceeded (day-run max ~0.345). n=1, not a reversal,
but exactly D2's revisit condition. Per D4 HN cosines are NOT comparable to the news-calibrated 0.35 — 7B.5 must
set a social threshold on social data. (Promoted out of the `hn_rejects.csv` footnote per Ron.)

### T12 — UNRESOLVED OOM mechanism + documentation defect (record, do NOT edit those files mid-run)

- **Unresolved question (Ron):** why did `LOG_INFO_SAMPLE_RATE=1.0` trigger the OOM if INFO is unsampled on the
  Flink UDF path either way (default 0.01 but empirically full — 14/14 startup lines, unsampled skip counts)?
  If absent already passes INFO unsampled, setting 1.0 cannot have *multiplied* record volume, so the "100×
  records over the Beam channel" story does **NOT hold**. The **empirical finding stands** (differentiating
  variable across 3 attempts, memory held at 2560Mi); the **mechanism is not established.** Candidate: the var
  changes which handlers `setup_logging()` installs → duplicate log *propagation*, not a higher sample rate.
  **Unverified.** (Ron corrected plan §8.5 to say the mechanism is not established — do not restate 100× as fact.)
- **Documentation defect:** `bringup_profiles.md` §5 trap 3 and `cluster_operations_guide.md` §11 both say INFO
  is 1%-sampled for "anything using `setup_logging()`, which includes the agent and the Flink jobs." The agent
  half is evidenced; the **Flink half looks false** (this run: full INFO on the UDF path). Trap 3 tells future
  sessions not to plan measurement around grepping INFO — possibly wrong for the pipeline. Record for T12.

### TEARDOWN CHECKLIST — CORRECTED (supersedes the earlier 6Gi note)

The 6Gi/4Gi resource edit was **reverted** — it is NOT a durable change. `LOG_INFO_SAMPLE_RATE` is now the
suspected OOM cause and stays **absent**. The DURABLE set at teardown is: **`-7d` image + `REJECT_CAPTURE=true`
+ `ENRICHMENT_DEDUP_GATE=true`** (RUN_ID reverted to neutral). **No resource change, no `LOG_INFO_SAMPLE_RATE`.**

## §8.3 step 10b — FIX ATTEMPT 1 (6Gi/4Gi) — FAILED. Re-diagnosis, escalated to Ron.

- Sequence executed: TM→0 + polymarket→0 + jobs cancelled; TM resources edited (limit 6Gi, req 4Gi);
  applied; new pod Ready (digest `sha256:9a73a780…`); a/b/c/d re-verified PASS on idle pod; jobs resubmitted
  (silver `723da304…`, gold `1096d959…`) → both RUNNING.
- **On load, the TM OOMKilled AGAIN at 6Gi** (exit 137, ~34s, restart count 4). **Spend still 0** (crashes
  before enrichment). Backlog replayed is **modest** — newsapi 188, hackernews 250, telegram 0, polymarket
  1100 (~1,500 msgs) — so **NOT volume-driven**. Node has ~20GB free, so headroom isn't the blocker.
- **Re-diagnosis:** `taskmanager.memory.process.size=2048m` budgets only the **JVM**; PyFlink's Beam **Python
  worker processes run OUTSIDE that budget** in the container. With 4 slots × parallelism 2 × many chained
  Python operators (~14 workers), each loading heavy imports (numpy/tiktoken/openai/langchain via
  `config.settings`), the concurrent footprint exceeds even 6Gi when all sources activate at once on a
  resubmit-from-`earliest`. Flink's process.size does not bound it, so raising the container limit is
  chasing an unbounded number.
- **This strongly resembles KG-A-9** (Gold checkpoint fragility / restart-replay storms) — which §11
  explicitly scopes OUT of 7D as "a genuine architectural change." `-7b5i` ran the day-run on **2.5Gi**, so
  it was almost certainly OOM-storming intermittently (the 3,993-calls-for-232-items storm is the signature);
  the synchronized all-source T0 + resubmit turned a marginal condition into a hard crash-loop.
- **HALTED (TM→0, jobs cancelled). Escalated to Ron — will not keep bumping memory blindly.**