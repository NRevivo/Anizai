# pipeline_sprints.md
> Domain: A — Pipeline
> Type: Sprints
> Last updated: 2026-06-15
> TL;DR: Live status of the pipeline — what's closed, the one open sprint (Phase 7B.5 calibration), deferred items, and the Domain A Known Gaps (KG-A-*). Open this to see what pipeline work remains.

## Navigation
- §1 — Status Summary — every pipeline phase, status, close date, outcome
- §2 — Open Work — Phase 7B.5 filter calibration
- §3 — Deferred Items — parked work and the condition to revisit
- §4 — Known Gaps — KG-A-* table

---

## §1 — Status Summary

| Sprint / Phase | Status | Closed | Key outcome |
|---|---|---|---|
| Phase 0 — Foundation | Closed | 2026-03 | Config, Kafka utils, validators, DB, `init.sql`, docker-compose |
| Sprints 1–10 — 9 source vertical slices | Closed | 2026-03 → 04 | All 9 producers + Silver/Gold branches + persistence, triple-gate tested |
| Sprint 11 — Pre-Phase 5 checkpoint | Closed | 2026-04-09 | 5 bugfixes; Reddit fully removed; prompts extracted to `prompts/` |
| Sprint 12 — Dockerfile.flink | Closed | 2026-04-10 | Custom PyFlink 1.19.1 image; EXACTLY_ONCE unblocked |
| Sprint 13 — Cross-cutting enrichment | Closed | 2026-04-10 | Translation, consensus helpers, mapping_dict, reactive trigger consumer |
| Sprint 14 — Airflow orchestration | Closed | 2026-04-11 | 7 scheduled DAGs; OpenSky OAuth2 migration |
| Sprint 15 — Production readiness | Closed | 2026-04-11 | OpenSky OAuth2 live-validated; orchestration test (scraper later retired in Phase 7C) |
| Sprint 16 — Monitoring | Closed | 2026-04-11 | Prometheus + Grafana; structured JSON logging; trace_id propagation |
| Sprint 17 — Full E2E live validation | Closed | 2026-04-11 | All 9 sources live Bronze→Gold→vaults; PyFlink side-output rewrite |
| Phase 7A — Provider migration | Closed | 2026-05-09 | NewsAPI → newsapi.ai (Event Registry); full article body to vault |
| Phase 7B — Filter + semantic rescue | Closed | 2026-05-09 | Two-stage gate; threshold 0.09→0.15; semantic rescue live |
| Phase 7C — Scraper retirement | Closed | 2026-05-09 | Scraper deleted; `scrape_attempted` dropped; `newspaper4k` removed |

---

## §2 — Open Work

### Phase 7B.5 — Filter-Threshold Calibration (queued)

**Goal:** Validate empirically the filter thresholds that Phase 7B set theoretically,
using real production vault data instead of pre-production estimates.

**Entry gate:** ≥200 post-7A `knowledge_vault` rows in cloud Postgres — **satisfied**
(616+ rows available).

| Task | Description |
|---|---|
| T7B.1 | Run `filter-analysis` skill on ≥200 post-7A full-body rows; confirm/trim the 10 A1 keyword removals; evaluate trimming political `GENERAL_KEYWORDS` now that `news/Politics` is its own category |
| T7B.2 | Threshold calibration — pull `relevance_score` distribution; confirm or replace `DEFAULT_THRESHOLD=0.15` |
| T7B.9 | Semantic-rescue calibration — classify rescued vs. dropped on 100 sniper-rejected rows; pick `GOLD_SEMANTIC_RESCUE_THRESHOLD` for ≥80% rescue precision (current default 0.35) |
| — | Produce the deferred `docs/phase7_filter_analysis.md` deliverable |

**Next action:** kick off with `sprint-kickoff` + `filter-analysis` skills against cloud
`knowledge_vault`. Plan detail: `docs/archive/phase7_intelligent_filtering.md`.

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

> KG-A-3, KG-A-4, and KG-A-5 are pipeline-producer concerns that were first **surfaced
> during Phase 9.5 (cluster robustness)**; they are owned here as Domain A ingestion gaps,
> not by the cloud domain.
