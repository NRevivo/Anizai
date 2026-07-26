# agent_cloud_run_20260726.md

> Domain: B — Agentic Hub
> Type: Report
> Last updated: 2026-07-26
> TL;DR: Results of the **agent-only cloud bring-up** of 2026-07-25/26 — the first
> real health proof of `anizai-agent:0.5.0-sprint26` in cloud, and the first
> real-vault, real-frontend forecast traffic. 7 forecasts, all `tier_2`, all `done`,
> ≈$0.183 total, ~27–30 s each. **This file exists because the source metrics are on
> a 7-day Prometheus retention and will be purged shortly after the next bring-up.**

## Navigation
- §1 — What this run was (and was not)
- §2 — Results
- §3 — Findings
- §4 — What this run did not cover

---

## §1 — What this run was

Not the "initial cloud test (~2 days)" of `hub_sprints.md` §1, and not Stage 2 of
`Claude-anizai-docs/b_deploy/b_cloud_deploy_two_stage.md` in full. It was Stage 2
**T2.2 only**: bring the Sprint-26 agent up in cloud, prove it healthy, and observe
real forecasts. T2.1 (the `anizai-airflow` rebuild) was deliberately skipped — see
§4.

**Configuration.** `main-pool` at 1 node; `flink-jobmanager`, `flink-taskmanager`,
`telegram` and `polymarket` held at 0; the 7 producer DAGs paused. Domain A was fully
idle for the duration, so no pipeline enrichment competed for the OpenAI account.
This is the AGENTS profile in `docs/guides/bringup_profiles.md`.

**Window.** Agent up 2026-07-25 16:04 UTC → 2026-07-26 ~11:48 UTC (~20 h). Traffic
arrived through the partner frontend against Firestore `anizai-ai`; the operator did
not hand-create documents.

**Vault.** Populated by the Domain-A day-run (`run_id=dayrun-20260722`). This is the
first agent measurement against a real vault — the Sprint-26 latency report (26.3)
used a mocked `vault_query` and was explicitly a mock floor.

**Health gate.** All six items passed: pod Ready with no restarts, `/health` 200,
image digest `sha256:7fce4e8b…c316ef4`, `AGENT_VERSION=0.5.0-sprint26+55e8093`, both
Firestore listeners attached (`forecastQueries` + the Sprint-24 `messages`
collection-group), and `/metrics` serving real exposition with Prometheus scraping it.

---

## §2 — Results

Source: `agent_llm_cost_usd_total` and `agent_node_duration_seconds` (Prometheus,
scraped, 7-day PVC retention). Copied here because that retention will delete them.

**Volume.** 7 forecasts completed. 7/7 `done`, 0 failures. **7/7 `tier_2`, 0
`tier_1`.**

**Cost — ≈$0.183 total, ≈$0.026 per forecast.**

| Model | Cost |
|---|---|
| gpt-4o | $0.1706 |
| gpt-4o-mini | $0.0126 |
| embeddings | $0.000001 |

gpt-4o accounts for ~93 % of spend. Cost work, if it ever becomes worth doing, is
almost entirely a `synthesize` question.

**Latency — per-node mean, n=7.**

| Node | Mean |
|---|---|
| `synthesize` | 13.7 s |
| `rate_evidence` | 8.6 s |
| `query_understand` | 1.5 s |
| `suggested_actions` | 1.26 s |
| all others | < 1.1 s |

End-to-end ≈27–30 s per forecast — **within the ≤60 s p95 NFR (KG-B-5)**, with
`synthesize` + `rate_evidence` at ~82 % of the total. That two-node dominance matches
the 26.3 mocked-vault finding (~85 % of ~35 s), which is the useful result here: the
real vault did not change the shape, and real-vault reads did not turn out to be the
hidden cost the mock floor left open.

**Sprint features exercised live.** Sprint 25 `generate_suggested_actions` ran 7×;
Sprint 22 `sentimentTimeSeries` populated (3 points); Sprint 24 follow-up path
processed a message end-to-end in cloud. The Firestore `sessions`/`sessionResults`
documents persist indefinitely and remain available for per-feature verification
whether or not the cluster is up.

---

## §3 — Findings

**1 — 0/7 `tier_1`.** Across 20 hours of real frontend questions, the Sprint-22
Polymarket literal-match path (pg_trgm) never fired. The whole Tier-1 branch —
`marketProbability`, market-data threading through `synthesize` — therefore remains
unverified in cloud despite a full-length run. Either the questions asked were simply
not market-shaped, or the literal match is too narrow. This is direct evidence toward
the FE 4 revisit condition (`hub_sprints.md` §3); 7 samples is thin, but 0/7 is a
signal rather than noise. Next agent session should include at least one deliberately
market-shaped question to separate the two explanations.

**2 — the numbers were nearly lost.** The plan for this session assumed cost and
latency would be read from the agent's `llm_usage` log lines. They were not there:
INFO is 1 %-sampled in cloud (`bringup_profiles.md` §5 trap 3, KG-B-4). The run was
saved by Sprint 26's Prometheus metrics (26.4) — an instrumentation task that was not
motivated by this at all. Cloud Logging held 7 entries for the whole 20-hour window.

**3 — two `forecastQueries` orphaned at `claimed`** (`e2e-sprint21-resume-*`, from
2026-05-05) are still unprocessed. Nothing scans that status, so they never
self-clear. Opened as KG-B-21; it bears directly on Sprint 27 task 27.6.

**4 — nine stale `sent` follow-up messages** across six historical sessions would
have been claimed and answered on agent startup, before any operator traffic. Caught
by a pre-flight gate and cleared under scoped authorization (status flipped
`sent` → `answered`; nothing deleted). This is now a standing gate in
`bringup_profiles.md` §3 Step 4.

---

## §4 — What this run did not cover

Recorded so that "the agent ran in cloud for 20 hours" is not later read as broader
coverage than it was.

- **Tier 1 / Polymarket matching** — never exercised (finding 1).
- **The reactive-ingestion round trip.** The agent's emit half is intact, but the
  deployed `anizai-trigger-consumer:0.1.0` predates Sprint 23 and its
  SecretProviderClass mounts no NewsAPI key, so the producer half cannot run. T2.1
  was skipped for this reason, which is also why the run was not blocked on the two
  `anizai-airflow` fold-in decisions.
- **Statistical p95** — n=7. The ≤60 s NFR is verified by 27.10's 50-session load
  test, not by this.
- **FE 3 / 5 / 8 revisit conditions** — all three are frequency judgements ("empty on
  too many forecasts", "consistently noisy"). 7 sessions can only catch gross
  failure; the conditions stay open.
- **Long-run worker stability** — no restart, re-claim, or listener-drop event
  occurred, which is not the same as demonstrating resilience. That is 27.5 / 27.6.
- **Cold-start and time-of-day variance** — a single continuous window.
