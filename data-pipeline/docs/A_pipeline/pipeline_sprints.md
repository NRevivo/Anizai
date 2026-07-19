# pipeline_sprints.md
> Domain: A — Pipeline
> Type: Sprints
> Last updated: 2026-07-18
> TL;DR: Live status of the pipeline — what's closed, the open sprints (Phase 7B.5 calibration + its 7B.5-I instrumentation prerequisite), deferred items, and the Domain A Known Gaps (KG-A-*). Open this to see what pipeline work remains.

## Navigation
- §1 — Status Summary — every pipeline phase, status, close date, outcome, plan file
- Phase Context / Rationale — why Phase 7B.5 exists (theoretical → empirical)
- §2 — Open Work — Phase 7B.5 filter calibration (reference → `plans/`)
- §3 — Deferred Items — parked work and the condition to revisit
- §4 — Known Gaps — KG-A-* table

---

## §1 — Status Summary

| Sprint / Phase | Status | Closed | Key outcome | Plan file |
|---|---|---|---|---|
| Phase 0 — Foundation | Closed | 2026-03 | Config, Kafka utils, validators, DB, `init.sql`, docker-compose | — |
| Sprints 1–10 — 9 source vertical slices | Closed | 2026-03 → 04 | All 9 producers + Silver/Gold branches + persistence, triple-gate tested | — |
| Sprint 11 — Pre-Phase 5 checkpoint | Closed | 2026-04-09 | 5 bugfixes; Reddit fully removed; prompts extracted to `prompts/` | — |
| Sprint 12 — Dockerfile.flink | Closed | 2026-04-10 | Custom PyFlink 1.19.1 image; EXACTLY_ONCE unblocked | — |
| Sprint 13 — Cross-cutting enrichment | Closed | 2026-04-10 | Translation, consensus helpers, mapping_dict, reactive trigger consumer | — |
| Sprint 14 — Airflow orchestration | Closed | 2026-04-11 | 7 scheduled DAGs; OpenSky OAuth2 migration | — |
| Sprint 15 — Production readiness | Closed | 2026-04-11 | OpenSky OAuth2 live-validated; orchestration test (scraper later retired in Phase 7C) | — |
| Sprint 16 — Monitoring | Closed | 2026-04-11 | Prometheus + Grafana; structured JSON logging; trace_id propagation | — |
| Sprint 17 — Full E2E live validation | Closed | 2026-04-11 | All 9 sources live Bronze→Gold→vaults; PyFlink side-output rewrite | — |
| Phase 7A — Provider migration | Closed | 2026-05-09 | NewsAPI → newsapi.ai (Event Registry); full article body to vault | — |
| Phase 7B — Filter + semantic rescue | Closed | 2026-05-09 | Two-stage gate; threshold 0.09→0.15; semantic rescue live | — |
| Phase 7C — Scraper retirement | Closed | 2026-05-09 | Scraper deleted; `scrape_attempted` dropped; `newspaper4k` removed | — |
| Phase 7B.5 — Filter-threshold calibration | Open (queued) | — | Empirically validate the 7B thresholds + A1 removals on production vault data | `plans/phase7b5_filter_calibration.md` |
| Phase 7B.5-I — Filter observability & cost instrumentation | Open (active) | — | Reject retention + `rescue_cosine` persistence + per-call LLM cost events/views; one-day cloud collection run produces the 7B.5 dataset + the pipeline-day cost number | `plans/phase7b5i_filter_observability_and_cost.md` |

> Closed rows have no `plans/` file — their detail lives in `pipeline_archive.md`
> (and, for old flat-format work, `../../task_plan_archive.md`).

---

## Phase Context / Rationale

Phase 7B (closed 2026-05-09) set the two-stage filter's thresholds **theoretically**
— before any production data existed. The 10 A1 keyword removals, the
`DEFAULT_THRESHOLD = 0.15` sniper floor, and the `GOLD_SEMANTIC_RESCUE_THRESHOLD = 0.35`
rescue cutoff were chosen from design reasoning against pre-production (snippet-era)
estimates. Phase 7B.5 exists to **validate those values empirically** against real
post-7A `knowledge_vault` rows, because the full-body article shape shifts the score
distributions and calibrating against pre-7A rows would produce stale thresholds. The
≥200-row entry gate is satisfied (616+ rows available), so the sprint is unblocked.

---

## §2 — Open Work

### Phase 7B.5-I — Filter Observability & Cost Instrumentation (active)

**Status:** Open / active (kicked off 2026-07-02).

Instrumentation prerequisite discovered 2026-06-30: the data 7B.5 needs (rejected
articles, rescue cosine scores) is never persisted, so 7B.5 cannot run against
today's vault alone. This sprint adds flag-gated reject retention, `rescue_cosine`
persistence, and permanent per-call LLM cost tracking, then collects one day of
cloud data. Self-contained plan: **`plans/phase7b5i_filter_observability_and_cost.md`**.

### Phase 7B.5 — Filter-Threshold Calibration (queued)

**Status:** Open / queued — data collection now gated on the 7B.5-I day-run.

**Entry gate:** ≥200 post-7A `knowledge_vault` rows in cloud Postgres — **satisfied**
(616+ rows available).

The full task table (T7B.1 / T7B.2 / T7B.9 + the `docs/phase7_filter_analysis.md`
deliverable), the concrete values under validation, the gate model, and the skills
all live in the self-contained plan: **`plans/phase7b5_filter_calibration.md`**. Kick
off there with `sprint-kickoff` + `filter-analysis`.

---

## §3 — Deferred Items

| Item | Deferred from | Reason | Condition to revisit |
|---|---|---|---|
| Market Divergence Alerts | Sprint 1 / Phase 4E | Only one active market platform after PredictIt removal; cross-platform divergence needs two | A second prediction-market platform is added |
| Polymarket comment ingestion | Phase 9.5 | Gamma `/comments` breaking change; correct `entity_entity_type` enum unknown | New API contract resolved (reverse-engineered or vendor support) |
| Re-embedding pre-Sprint-15 truncated rows | Sprint 15 | Resolved by Phase 7A full-body migration — accepted as the new normal, no longer a deferral | n/a (closed) |

---

## §4 — Known Gaps

| ID | Description | Raised in | Priority | Condition to address |
|---|---|---|---|---|
| KG-A-1 | `mapping_dict.find_similar_and_link()` not wired into the Gold pipeline; cross-source canonical linkage is not automatic | Sprint 13 / 15 | Medium | Live pipeline perf data to choose approach A (on-insert) vs B (batch scan) |
| KG-A-2 | `PolymarketGoldMetricsFunction` misnamed — now dispatches 5 metric sources; cosmetic only | Sprint 17 | Low | Any `gold_job.py` refactor |
| KG-A-3 | Google Trends producer 404 — pytrends hits Google's moved unofficial endpoint, 0 Bronze messages (raise-on-0% mitigates silent success) | Surfaced Phase 9.5 | Medium | Upstream pytrends fix, switch to official Trends API, or retire the source |
| KG-A-4 | Polymarket `/comments` breaking change; comment ingestion feature-flagged off | Surfaced Phase 9.5 | Medium | Reverse-engineer the new `/comments` contract, or retire the comment path |
| KG-A-5 | OpenSky outbound timeout from GKE (`opensky-network.org` unreachable from main-pool node); local ingestion works | Surfaced Phase 9.5 | Medium | GCP firewall/CIDR investigation; possible cloud-IP block by OpenSky |
| KG-A-6 | Dormant `reddit`/`predictit` enum values remain in PostgreSQL CHECK constraints (`init.sql`, `postgres-configmap.yaml`); no active writer | Phase 7 doc note | Low | A later infra/schema migration phase |
| KG-A-7 | `knowledge_vault` dedup does not gate Gold enrichment — duplicate global_news articles re-run GPT enrichment + embedding on every re-fetch (cost + RPD impact) | Phase 7B.5-I | Medium | Exists-check before Gold dispatch (pattern exists in the social path) |
| KG-A-8 | global_news builders use `uuid4` signal_id — Flink re-deliveries and duplicate articles accumulate duplicate `knowledge_vectors` rows | Phase 7B.5-I | Medium | Deterministic UUID5 from content_hash (pattern exists in the social path) |
| KG-A-9 | Gold checkpoint fragility under dense backlog — synchronous ~1–2s enrichment stalls barriers → expiry → restart replay loops (2026-07-02 storm: 3,993 calls for 232 unique items) | Phase 7B.5-I | High | Checkpoint tuning / unaligned checkpoints / async enrichment |
| KG-A-10 | Local compose Flink leg broken — Beam Python-worker crash on first message (env gap since ~Sprint 17); in-process replay is the current local verification path | Phase 7B.5-I | Low | Rebuild/realign the local PyFlink worker env; not cloud-affecting |
| KG-A-11 | Phase-7C scraper retirement removed `newspaper4k` from the manifests but did not fully clean the environment. `newspaper4k==0.9.5` lingered as an orphan in the local dev venv (absent from `requirements.txt`/`.lock`, imported by no code), and several of its transitive deps remain in `requirements.lock` as possible co-orphans (`lxml_html_clean`, `requests-file`, `tldextract`, `w3lib` — note `lxml`/`beautifulsoup4` are shared and are NOT co-orphans). The venv orphan was uninstalled during Sprint 26 (Domain B, 26.4, `pip uninstall newspaper4k`); the lock-side co-orphans were deliberately left untouched then to avoid dependency-surface movement before the baseline day-run. | Sprint 26 (surfaced by the 26.4 dependency add) | Low | A scoped Phase-7C venv/lock hygiene pass, post baseline day-run — audit each candidate co-orphan for remaining importers and remove only the truly unused. Do NOT `pip freeze` the dev venv before that audit (it would rewrite the whole lock). |

> KG-A-3, KG-A-4, and KG-A-5 are pipeline-producer concerns that were first **surfaced
> during Phase 9.5 (cluster robustness)**; they are owned here as Domain A ingestion gaps,
> not by the cloud domain.
