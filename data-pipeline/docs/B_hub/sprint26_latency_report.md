# sprint26_latency_report.md
> Domain: B — Agentic Hub
> Type: Analysis report (KG-B-5)
> Last updated: 2026-07-18
> TL;DR: Per-node latency profile for the main forecast graph (Sprint 26 T26.3,
>        **analysis only**). Two token-bound nodes — `synthesize` (~16–19s) and
>        `rate_evidence` (~12–14s) — dominate ~85% of the ~35s end-to-end; every
>        other node is sub-3s. Both measured scenarios sit **within the ≤60s p95
>        NFR** (KG-B-5, relaxed 2026-07-04). No O(1)-regression candidates found.
>        `vault_query` is a **mock floor** here; the authoritative real-vault +
>        cold/warm p95 is the cloud baseline day-run.

## Navigation
- §1 — Method + the conscious deviation from the ratified T20.11/T21.12 mechanism
- §2 — Per-node results (both scenarios)
- §3 — Classification (token-volume-bound vs O(1)) + read against the NFR
- §4 — agentEvents / drain overhead
- §5 — Caveats + what the cloud baseline day-run must still measure
- §6 — A 26.4 gap this analysis surfaced (+ fixed)

---

## §1 — Method

**Driver:** `tests/e2e/sprint26_latency_run.py`. Emulator (Firestore, localhost:8080)
+ **real OpenAI** + **realistic-volume mocked retrieval agents**. Two forecasts —
one Tier-1 (Polymarket-backed), one Tier-2 (freeform, higher volume) — run through
`process_query` on the real graph; per-node durations read from the Sprint-26
`agent_node_duration_seconds` histogram (T26.4) and cross-checked against
`agentEvents.durationMs`. Results: `tests/e2e/sprint26_latency_results.json`.

**Conscious deviation from the ratified "run T20.11 + T21.12" mechanism**
(Advisor↔Ron 2026-07-18). The literal E2E drivers (`sprint20_e2e_run.py` /
`sprint21_e2e_run.py`) require a running worker **+ real Postgres vault** purely to
time `vault_query` — the low-seconds parallel-DB node that is **not** the
bottleneck. This driver obtains the **same node coverage** without Postgres. The
tradeoff: `vault_query` is a mock floor (§5). The token-bound nodes that dominate
latency get realistic timing from real OpenAI alone, which is what this report is
for. Mocking is calibrated to a real Sprint-20 forecast's evidence volume, and the
market mock injects every `structured_intent.entities` term into a fred anomaly's
`anomaly_flags` so `sufficiency_check` stays on the **sufficient** path (both runs
confirmed: `sufficient_path=True`, no `trigger_reactive_ingestion`).

**Scenarios** (one Tier-1 + one Tier-2 — *not* a redundant cold/warm second pass;
cold/warm is a vault-cache effect erased by mocking, deferred to the day-run):

| Scenario | Question | Tier | Evidence items |
|---|---|---|---|
| `tier1` | "Will the Federal Reserve cut interest rates by Q2 2026?" | tier_1 | 11 |
| `tier2_broad` | "Will the United States enter a recession in 2026?" | tier_2 | 15 |

---

## §2 — Per-node results

Primary dataset (run 3; `agentEvents.durationMs` ≈ histogram to <1ms — the two
sources cross-validate, so the 26.4 histogram is accurate). Durations in seconds.

| Node | tier1 (s) | tier2_broad (s) | Bound |
|---|---|---|---|
| claim_session | — (bootstrap, not instrumented by design) | — | fixed I/O |
| query_understand | 2.59 | 1.33 | LLM (small) |
| build_embedding | 0.77 | 0.39 | LLM embed (small) |
| vault_query | 0.03 **(mock floor)** | 0.00 **(mock floor)** | I/O (parallel) |
| sufficiency_check | ~0.00 | ~0.00 | **O(1) deterministic (no LLM)** |
| rate_evidence | **14.28** | **12.16** | **token-volume-bound** |
| synthesize | **15.95** | **19.26** | **token-volume-bound** |
| generate_suggested_actions | 2.20 | 1.72 | LLM (small) |
| write_to_firestore | 0.06 | 0.03 | I/O (Firestore) |
| **End-to-end total** | **35.95** | **34.94** | — |

**Run-to-run variance (real-OpenAI-driven).** A prior full pass measured tier1
**29.91s** / tier2 **31.58s** — the delta is almost entirely `synthesize`
(14.0→16.0s) and `rate_evidence` (9.8→14.3s). OpenAI-side latency on the two
dominant nodes varies ~±20–40% run-to-run, so a real p95 needs many samples (the
day-run), not n=1–2. These numbers are **indicative of shape**, not a p95.

---

## §3 — Classification + NFR

**Token-volume-bound (expected — scale with evidence volume + output tokens):**
- **`synthesize` (~16–19s)** — the single largest node; one gpt-4o reasoning call
  over all rated evidence + the output (probability, key factors, gaps, markdown).
  Grows with evidence item count and output length. Expected dominant.
- **`rate_evidence` (~12–14s)** — gpt-4o-mini, batched 8 items/call, so cost scales
  ~linearly with evidence item count (11 items → 2 batches; 15 → 2 batches).
  Expected second.

Together **~85% of end-to-end** (~28–31s of ~35s). Any latency-optimization work
(deferred to Sprint 27 / Phase 10) must target these two — everything else is
noise by comparison.

**Small LLM nodes (sub-3s, roughly fixed):** `query_understand` (1.3–2.6s),
`generate_suggested_actions` (1.7–2.2s), `build_embedding` (0.4–0.8s). Single
small-context calls; not volume-sensitive at these evidence sizes.

**O(1) / I/O (not regression candidates):**
- `sufficiency_check` **~0.00s** — confirmed **deterministic, no LLM** (rubric over
  evidence counts + entity substring match). As designed.
- `vault_query` **0.00–0.03s** — **mock floor** (§5). Real value is low-seconds
  parallel DB I/O, bounded by `PER_AGENT_TIMEOUT_S=15s`; not the bottleneck.
- `write_to_firestore` **0.03–0.06s** (emulator; incl. drain — §4).

**No O(1)-regression candidates.** Every node behaves as its design predicts:
the token-bound nodes dominate, the deterministic/I-O nodes are fast.

**Against the ≤60s p95 NFR (KG-B-5, relaxed 2026-07-04):** both scenarios land
**~35s**, comfortably inside 60s — **but** this excludes real `vault_query` latency
(§5). Adding a realistic low-seconds `vault_query` + cloud/network variance on the
Firestore writes and the (variable) OpenAI calls, a real broad-case p95 could reach
~40–50s — still within 60s, but the broad case retains the least headroom, matching
the pre-existing KG-B-5 note (47s broad measurement). **No optimization is required
to pass the NFR for the initial test**; the token-bound-node work stays deferred
(FE 7 / Phase 10) unless the day-run crosses 60s p95.

---

## §4 — agentEvents / drain overhead

`agentEvents` is emitted fire-and-forget (a background writer thread), so per-node
emission adds **negligible** critical-path time — confirmed by `agentEvents.durationMs`
matching the histogram (which brackets only the node body) to <1ms on every node.

The one blocking agentEvents cost is the **pre-`done` drain** inside
`write_to_firestore` (waits for the event queue to flush before flipping `done`,
bounded by `AGENT_EVENT_DRAIN_TIMEOUT_MS`). Isolated via the gap between
`write_to_firestore`'s event duration (pre-drain, at `complete_event`) and its
histogram duration (full, post-drain + status writes): **~0–1ms** on the emulator
(queue already flushed, small event count). This is **network-dependent** — on
cloud Firestore with real round-trip latency and ~10 events, the drain + the two
status writes (steps 5–6) will be larger, though still bounded by the drain
timeout. The day-run should re-check this on real Firestore.

---

## §5 — Caveats + what the day-run must still measure

- **`vault_query` is a MOCK FLOOR.** Agents returned synthetic evidence with no DB
  I/O, so `vault_query` reads ~0ms. Real value = three parallel retrieval agents
  doing ~10 Postgres queries each, low-seconds, bounded at 15s/agent. **The
  authoritative real-vault latency is the cloud baseline day-run** (real Postgres +
  real vault) — that is the true KG-B-5 measurement; this report is the
  token-bound-node profile that the day-run cannot easily isolate.
- **Cold vs warm deferred.** Cold/warm is a vault-cache-layer effect; mocking the
  vault erases it, so both passes would measure identically — not spent here. The
  day-run (real vault) measures it.
- **n=1–2 per scenario.** Real-OpenAI variance on the dominant nodes is ±20–40%;
  these numbers show *shape*, not p95. The day-run's many forecasts give the p95.
- **Firestore = emulator, local.** `write_to_firestore` + the drain are understated
  vs cloud Firestore round-trips (§4).

---

## §6 — A 26.4 gap this analysis surfaced (and fixed)

The first run showed `generate_suggested_actions` with an `agentEvents` duration but
**no histogram value**. Root cause: it is wired to emit `agentEvents` **manually**
(not via the `@events.emits` decorator — its docstring line 51), so it fell outside
the 26.4 histogram hook — exactly like `write_to_firestore`, but the plan's §2 had
assumed it was one of the "9 decorated pair-nodes." Since it is a load-bearing
token-bound node (~1.7–2.2s) the report must cover, a **manual `.observe()`** was
added to `generate_suggested_actions.run()` (success path, mirroring
`write_to_firestore`), with a Gate-1 test
(`test_generate_suggested_actions_observes_node_duration`). The final run confirms
it now appears in the histogram. This is a completeness fix to 26.4, surfaced by the
26.3 measurement — no change to the metric's contract.

---

## Cross-references
- **KG-B-5** (`hub_sprints.md §4`) — this report is its Sprint-26 analysis
  deliverable. Re-escalate if the day-run crosses 60s p95, or at Phase 10 load.
- Driver: `tests/e2e/sprint26_latency_run.py`; raw data:
  `tests/e2e/sprint26_latency_results.json`.
- Deferred optimization: `hub_sprints.md §3` (FE 7 — performance optimization).
