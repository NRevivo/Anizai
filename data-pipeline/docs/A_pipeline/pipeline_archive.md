# pipeline_archive.md
> Domain: A — Pipeline
> Type: Archive
> Last updated: 2026-06-15
> TL;DR: Append-only record of every closed pipeline sprint (Sprints 1–17, Phase 7A/7B/7C) — outcome, key decisions, tasks, and gaps raised. Open this for the full history behind a closed pipeline component.

**Append-only.** Never edit an existing entry once written; add new entries at the end.

**Historical phase-name note.** Internal planning docs used "Phase C" for what is now
**Phase 9** (cloud deployment, Domain C). Pipeline phases use current numbering throughout
this file: Phase 0–7 are pipeline; Phase 8 is the Agentic Hub (Domain B); Phase 9 is cloud
(Domain C); Phase 10 is calibration (Domain D).

---

## Archive Index

| Sprint | Date | Key decisions summary |
|---|---|---|
| Sprint 1 — Polymarket | 2026-03-29 | First streaming slice; social_vectors + momentum_vault patterns established |
| Sprint 2 — FRED | 2026-03-29 | First REST-polling slice; 9 indicator series; automation triggers |
| Sprint 3 — NewsAPI | 2026-04-01 | knowledge_vault/vectors split; Cognitive Metadata + keyword sniper born |
| Sprint 4 — ArXiv | 2026-04-01 | Academic domain_context; no impact boost; abstract-as-lede |
| Sprint 5 — Telegram | 2026-04-01 | MTProto streaming; routed to global_news; Direct Message domain_context |
| Sprint 6 — HackerNews | 2026-04 | Story-Centric social store; reused consensus path |
| Sprint 7 — PredictIt | 2026-04 | Permanently blocked (CFTC API shutdown); code removed |
| Sprint 8 — Google Trends | 2026-04 | Pytrends static + reactive + backfill; Public_Hype_Alert |
| Sprint 9 — OpenWeather | 2026-04-02 | temperature as current_value; condition→severity; DLQ-on-missing |
| Sprint 10 — OpenSky | 2026-04-04 | aircraft density; 7 boxes; cold-start trigger suppression |
| Sprint 11 — Pre-Phase 5 checkpoint | 2026-04-09 | 5 bugfixes; Reddit removal; prompts/ extraction |
| Sprint 12 — Dockerfile.flink | 2026-04-10 | Custom PyFlink 1.19.1 image; EXACTLY_ONCE unblocked |
| Sprint 13 — Cross-cutting enrichment | 2026-04-10 | Translation, consensus, mapping_dict, reactive consumer |
| Sprint 14 — Airflow orchestration | 2026-04-11 | 7 DAGs; OpenSky OAuth2 migration |
| Sprint 15 — Production readiness | 2026-04-11 | Scraping layer (later retired); OpenSky live-validated |
| Sprint 16 — Monitoring | 2026-04-11 | Prometheus/Grafana; JSON logging; trace_id |
| Sprint 17 — Full E2E validation | 2026-04-11 | PyFlink side-output rewrite; all 9 sources live |
| Phase 7A — Provider migration | 2026-05-09 | NewsAPI → newsapi.ai; full body |
| Phase 7B — Filter + semantic rescue | 2026-05-09 | Two-stage gate; threshold raise; drop semantics |
| Phase 7C — Scraper retirement | 2026-05-09 | Scraper + column + dependency removed |

---

## Sprint 1 — Polymarket (2026-03-29)

**Outcome.** First end-to-end vertical slice (Bronze→Silver→Gold→storage) over a real-time
WebSocket source. Established the `social_vectors` (HNSW) and `momentum_vault` (TimescaleDB)
persistence patterns reused by all later sources.

**Key Decisions.**
1. `knowledge_vault`/`social_vault` split — direct signals vs. community discourse have different recall patterns.
2. Consensus Bundling — group comments into temporal blocks; one Consensus Vector per block, not per message.
3. E2E used synthetic injected comments (Gamma `/comments` returned empty arrays); price path validated live.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| WebSocket/REST producer | `ingestion/polymarket_producer.py` |
| Silver + Gold branches | `processing/silver_job.py`, `gold_job.py` |
| Social vault + vectors + momentum vault | `persistence/social_vault.py`, `social_vectors.py`, `momentum_vault.py` |
| Kafka utils, validators (cross-cutting) | `utils/kafka_utils.py`, `validators.py` |

**Known Gaps Raised.** Dockerfile.flink + EXACTLY_ONCE deferred to Sprint 2 (standalone runner used).

---

## Sprint 2 — FRED (2026-03-29)

**Outcome.** First scheduled REST-polling slice; 9 macro/commodity/risk series flow to the
momentum vault with deterministic automation triggers. Established the poller pattern.

**Key Decisions.**
1. Series fix — `GOLDAMGBD228NLBM` → `DHHNGSP` (London Gold Fix returned HTTP 400; Henry Hub substituted).
2. Sentinel "." observations filtered; stable monthly series emit zero deltas by design.
3. `.env` fallback to `infrastructure/.env` (no manual copy).

**Tasks Completed.**
| Deliverable | File |
|---|---|
| REST producer (pulse + backfill, 9 series) | `ingestion/fred_producer.py` |
| Silver + Gold branches + triggers | `processing/silver_job.py`, `gold_job.py` |
| momentum_vault helpers | `persistence/momentum_vault.py` |

**Known Gaps Raised.** None new.

---

## Sprint 3 — NewsAPI (2026-04-01)

**Outcome.** First `knowledge_vault`/`knowledge_vectors` slice; created the Gold Global
Signal schema and Cognitive Metadata Extraction pattern reused by ArXiv and Telegram.

**Key Decisions.**
1. Impact Boost is NewsAPI-only (breaking-news authority whitelist); academic/Telegram excluded.
2. Tactical `domain_context` (4 keys); shared 10-field Cognitive Metadata prompt.
3. Keyword Sniper + SHA-256 dedup born here (`DEFAULT_THRESHOLD=0.09` at this stage).

**Tasks Completed.**
| Deliverable | File |
|---|---|
| REST producer + authority whitelist | `ingestion/newsapi_producer.py` |
| Keyword sniper, deduplication | `processing/keyword_sniper.py`, `deduplication.py` |
| Knowledge vault + vectors | `persistence/knowledge_vault.py`, `knowledge_vectors.py` |

**Known Gaps Raised.** None new. (Provider later migrated — Sprint 21.5, then Phase 7A.)

---

## Sprint 4 — ArXiv (2026-04-01)

**Outcome.** Second knowledge source; introduced the Academic `domain_context` shape with a
separate Gold builder. Reused Sprint 3 persistence with no reimplementation.

**Key Decisions.**
1. No Impact Boost, no authority whitelist (single origin, arxiv.org).
2. Separate `build_arxiv_gold_global_signal()` — Academic vs. Tactical schemas kept independently testable.
3. `full_text_raw == inverted_pyramid_lead == abstract`; canonical (versionless) URL as dedup key.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Atom XML producer (7 categories) | `ingestion/arxiv_producer.py` |
| Silver + Gold branches | `processing/silver_job.py`, `gold_job.py` |

**Known Gaps Raised.** None.

---

## Sprint 5 — Telegram (2026-04-01)

**Outcome.** MTProto streaming source; third knowledge_vault source. Caught and fixed a
routing bug (Telegram → global_news, not social_pulse) before Silver implementation.

**Key Decisions.**
1. Telegram → `knowledge_vault` (vetted OSINT direct signals, not community discourse).
2. No Impact Boost (channel authority is structural — the 7-entry registry).
3. `http_status_code=0`, `request_duration_ms=0` for the push transport.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Telethon producer (7 channels) | `ingestion/telegram_producer.py` |
| Routing fix | `config/kafka_topics.py` |
| Silver + Gold branches | `processing/silver_job.py`, `gold_job.py` |

**Known Gaps Raised.** Translation for non-English channels deferred to Phase 5.

---

## Sprint 6 — HackerNews (2026-04)

**Outcome.** Story-Centric social source reusing the consensus/social_vectors path with no
new validator. `points > 50`, top 10 comments per story.

**Key Decisions.**
1. Reused `social_vault`/`social_vectors`/`validate_silver_social()` HackerNews branch (pre-existing from Phase 0).
2. `entry_type = "hackernews_story_summary"` with platform extensions.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Algolia producer | `ingestion/hackernews_producer.py` |
| Silver + Gold branches | `processing/silver_job.py`, `gold_job.py` |

**Known Gaps Raised.** None.

---

## Sprint 7 — PredictIt (2026-04) [permanently blocked]

**Outcome.** Producer and Gate 1–3 tests implemented per spec, but PredictIt's public API
was shut down by CFTC action; all code removed. E2E never ran (data permanently unavailable).

**Key Decisions.**
1. One Bronze envelope per contract (not per market).
2. `is_new_market=True` when `last_trade_price is None` vs `0.0` — avoids spurious deltas.
3. Removal follows the same pattern later used for Reddit.

**Tasks Completed.** All implemented then deleted (`predictit_producer.py`, Silver branch, mocks, Gate 1–3 tests).

**Known Gaps Raised.** Cross-platform Divergence Alerts become dormant (single market platform remains).

---

## Sprint 8 — Google Trends (2026-04)

**Outcome.** Static (daily top-50, 4 geos) + reactive + 5-year backfill modes to the
momentum vault; deterministic `Public_Hype_Alert`. No OpenAI.

**Key Decisions.**
1. Rank-to-100 score normalization formula.
2. Reactive mode wired for on-demand agent keyword requests.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Pytrends producer | `ingestion/googletrends_producer.py` |
| Silver + Gold branches + trigger | `processing/silver_job.py`, `gold_job.py` |

**Known Gaps Raised.** None at the time (pytrends 404 surfaced later in Phase 9.5 → KG-A-3).

---

## Sprint 9 — OpenWeather (2026-04-02)

**Outcome.** 10 strategic hotspots, static + reactive, to the momentum vault with three
deterministic triggers. No backfill (free-tier OWM has no History API).

**Key Decisions.**
1. `current_value=temperature_celsius`; other measurements in `metadata_extension`.
2. Map `weather[0].id`→`condition_severity`, `wind.speed` m/s→knots; missing arrays → DLQ (never zero-default).
3. Cold-start (`is_new_market=True`) suppresses all triggers.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| OWM producer (10 hotspots) | `ingestion/openweather_producer.py` |
| Silver + Gold branches + 3 triggers | `processing/silver_job.py`, `gold_job.py` |

**Known Gaps Raised.** No historical baseline at cold start (accepted limitation).

---

## Sprint 10 — OpenSky (2026-04-04)

**Outcome.** Aircraft-density source over 7 bounding boxes, static + reactive, with two
deterministic triggers. Real-time only; 30-day baseline accrues organically.

**Key Decisions.**
1. `current_value=aircraft_density_count`; `external_reference_id=bounding_box_id`.
2. Compact `states_compact` (5 fields/aircraft); Silver computes `transponder_silence_events`.
3. `change_30d` fractional; `Aerial_Escalation_Risk` at `>0.30`; cold-start suppression.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| OpenSky producer (7 boxes) | `ingestion/opensky_producer.py` |
| Silver + Gold branches + 2 triggers | `processing/silver_job.py`, `gold_job.py` |

**Known Gaps Raised.** None at the time (OAuth2 migration in Sprint 14; GKE reachability surfaced Phase 9.5 → KG-A-5).

---

## Sprint 11 — Pre-Phase 5 Checkpoint (2026-04-09)

**Outcome.** Bugfix/gap-closure sprint: five targeted fixes plus the complete removal of
Reddit. No new features; 321/321 then 266/266 Gold-gate tests green.

**Key Decisions.**
1. `silver_data_ref` default = `None` (not `bronze_ref`) — correct sentinel for "not yet wired".
2. Deterministic `signal_id = uuid5(content_hash)` — makes `ON CONFLICT DO NOTHING` dedup actually work.
3. Reddit fully removed (PredictIt precedent); prompts extracted to a new `prompts/` package.

**Tasks Completed.**
| Task | Files |
|---|---|
| silver_data_ref wiring | `social_vault.py`, `gold_job.py`, `validators.py` |
| Deterministic signal_id | `gold_job.py` |
| Polymarket non-last_trade skip | `silver_job.py` |
| Reddit removal (16+ files) | `config/*`, `processing/*`, `persistence/*`, tests |
| Prompts extraction | `prompts/cognitive_metadata.py`, `consensus_summary.py` |

**Known Gaps Raised.** None new (keyword_sniper analysis deliberately scoped out → later Phase 7).

---

## Sprint 12 — Dockerfile.flink (2026-04-10)

**Outcome.** Built the custom `anizai-flink:1.19.1` image (Python 3.11 + PyFlink 1.19.1 +
Kafka uber-JAR + source), unblocking all Phase 5 processing. EXACTLY_ONCE confirmed.

**Key Decisions.**
1. Python 3.11 (PyFlink 1.19 supports ≤3.11; do not move to 3.12 without Flink 1.20+).
2. `setuptools==70.3.0` pin (81+ removed `pkg_resources`, which apache_beam imports).
3. Same image for JobManager + TaskManager (identical operator bytecode); COPY source at build.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Flink image | `infrastructure/Dockerfile.flink` |
| Compose build wiring | `infrastructure/docker-compose.yml`, `.dockerignore` |
| Smoke tests | `tests/test_flink_smoke.py`, `test_flink_kafka_connectivity.py` |

**Known Gaps Raised.** `translation.py` blocker removed; implementation deferred to Sprint 13.

---

## Sprint 13 — Cross-Cutting Enrichment (2026-04-10)

**Outcome.** Delivered the four Phase 5 cross-cutting modules: Silver-layer translation,
temporal consensus helpers, the mapping dictionary, and the reactive ingestion consumer.

**Key Decisions.**
1. Translation at Silver via GPT-3.5-turbo; allowlist `abualiexpress→he`, `yediotnews25→he` only.
2. Hash on original text; sniper runs on translated text.
3. mapping_dict delivered standalone — Gold-job integration deferred (needs live perf data).
4. Trigger consumer dispatches `run_reactive()` in daemon threads; lazy producer imports.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Translation | `prompts/translation.py`, `processing/translation.py`, `silver_job.py` |
| Consensus helpers | `processing/consensus.py` |
| Mapping dictionary | `persistence/mapping_dict.py` |
| Reactive trigger consumer | `orchestration/ingestion_trigger_consumer.py` |

**Known Gaps Raised.** mapping_dict Gold-job integration deferred to Phase 7 (→ KG-A-1).

---

## Sprint 14 — Airflow Ingestion Orchestration (2026-04-11)

**Outcome.** Apache Airflow stack with 7 DAGs for all scheduled producers, plus a
mid-sprint OpenSky OAuth2 migration (Basic Auth removed by OpenSky March 2026).

**Key Decisions.**
1. PythonOperator + `main(mode=...)` entry points; LocalExecutor; deferred imports inside callables.
2. Daily DAGs staggered 06/07/08 UTC; OpenSky 3-min cadence (per-day cap is the binding limit).
3. OpenSky OAuth2 client_credentials with proactive token refresh.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Airflow image + deps | `infrastructure/Dockerfile.airflow`, `requirements-airflow.txt` |
| 7 DAGs | `orchestration/dags/*.py` |
| OpenSky OAuth2 | `ingestion/opensky_producer.py`, `config/settings.py` |

**Known Gaps Raised.** None (OpenSky live OAuth2 validation deferred to Sprint 15).

---

## Sprint 15 — Production Readiness (2026-04-11)

**Outcome.** Added a post-Silver article-scraping layer, live-validated OpenSky OAuth2, and
confirmed the FRED Airflow→Kafka→vault chain end-to-end. (The scraping layer was later
retired wholesale in Phase 7C once newsapi.ai supplied full body text.)

**Key Decisions.**
1. Scraping ran POST-Silver (producer/Silver unchanged), enriching `full_text_raw` asynchronously.
2. Domain-based whitelist (6 scrapable outlets); CNN excluded (JS-rendered garbage).
3. `scrape_attempted` boolean to prevent infinite retry loops.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Scraping layer (later deleted Phase 7C) | `utils/article_scraper.py`, `orchestration/scraper_runner.py`, `scraper_dag.py` |
| `scrape_attempted` column (later dropped) | `infrastructure/sql/init.sql`, migration `001` |
| OpenSky live OAuth2 validation | `tests/e2e/run_opensky_e2e.py` |

**Known Gaps Raised.** Gold embeddings on truncated pre-Sprint-15 NewsAPI text (later resolved by Phase 7A full-body migration; accepted as new normal in Phase 7C).

---

## Sprint 16 — Pipeline Monitoring (2026-04-11)

**Outcome.** Prometheus + Grafana metrics stack, structured JSON logging with 1% INFO
sampling, and distributed `trace_id` propagation across every pipeline layer.

**Key Decisions.**
1. Flink native PrometheusReporter (JVM-level, port 9249) — no Python job changes.
2. `set_trace_id()` injected once inside `build_envelope()` (DRY) so all producers bind trace_id.
3. `canonical_event_id` (or `bronze_ref`) as the Gold-layer correlation ID (trace_id not propagated past Silver).

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Prometheus + Grafana | `infrastructure/docker-compose.yml`, `prometheus.yml`, `grafana/` |
| Logging config | `utils/logging_config.py` |
| Instrumentation | `silver_job.py`, `gold_job.py`, all persistence + producers |

**Known Gaps Raised.** None.

---

## Sprint 17 — Full End-to-End Live Validation (2026-04-11)

**Outcome.** Brought all 9 sources to a confirmed running state with real data; fixed every
bug surfaced during validation (no features deferred). Heavy PyFlink-API rewrite.

**Key Decisions.**
1. Tagged tuple routing replaces Java-only `OutputTag`/`ctx.output()`/`.returns()` in PyFlink 1.19.
2. `structured_metrics` topics switched `compact`→`delete` (Flink emits keyless messages).
3. Added `GlobalNewsGoldFunction` + source_name dispatch; wired all 9 sources in `build_pipeline()`.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Silver/Gold PyFlink rewrite + full wiring | `processing/silver_job.py`, `gold_job.py` |
| Validation tooling | `.env.example`, `docs/VALIDATION_GUIDE.md`, `tests/e2e/run_full_validation.py` |
| Infra fixes | `infrastructure/docker-compose.yml`, `Dockerfile.flink`, `utils/kafka_utils.py` |

**Known Gaps Raised.** `PolymarketGoldMetricsFunction` misnamed (now handles 5 sources) → KG-A-2.

---

## Phase 7A — Provider Migration to newsapi.ai (2026-05-09)

**Outcome.** Migrated the NewsAPI producer from TheNewsAPI to **newsapi.ai (Event Registry)**,
gaining full article `body` (replacing a 60-char snippet) reaching `knowledge_vault.full_text_raw`.
The producer was the API-shape boundary, so Silver/Gold/persistence and their Gate 2/3 tests
passed unchanged. E2E: 45/45 articles through Bronze→Gold, 0 DLQ.

**Key Decisions.**
1. Single endpoint `eventregistry.org/api/v1/article/getArticles`; auth via `apiKey`/`NEWSAI_API_KEY`.
2. `news` root category returns 0 results → dropped; explicit `news/Politics` added (5 categories total); producer `GENERAL_KEYWORDS` pre-filter removed.
3. `sourceUri` domain whitelist (list-of-tuples format); all TheNewsAPI constants removed in the same sprint.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Producer rewrite | `ingestion/newsapi_producer.py` |
| Config | `config/settings.py` (`NEWSAI_*`) |
| Fixtures/tests | `tests/mocks/newsapi_article.json`, Gate 1 tests |

**Known Gaps Raised.** Free-tier body truncation risk (accept-and-document, not deferred).

---

## Phase 7B — Filter Improvements + Semantic Rescue (2026-05-09)

**Outcome.** Replaced the single-layer keyword filter with a two-stage gate: a tighter
deterministic sniper plus an embedding-based semantic rescue. Articles failing both stages
are now dropped entirely (no `kv_archive`); 238 tests green.

**Key Decisions.**
1. Removed 10 noisy single-word keywords (A1); raised `DEFAULT_THRESHOLD` 0.09→0.15 (A2). *Reason for the raise: the 0.09 floor was too permissive — under full-body articles it admitted too much low-signal noise; the semantic-rescue layer recovers valid borderline articles the stricter floor would drop.*
2. Semantic rescue via `text-embedding-3-small` against committed `sniper_reference_vector.npy`; `GOLD_SEMANTIC_RESCUE_THRESHOLD=0.35` (env-overridable).
3. Fail-loud on missing reference vector; embedding-API failure during rescue → DLQ.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Cleaner keyword list + threshold | `processing/keyword_sniper.py` |
| Reference vector builder + artifact | `processing/build_sniper_reference_vector.py`, `sniper_reference_vector.npy` |
| Two-stage gate in Gold | `processing/gold_job.py` (`GlobalNewsGoldFunction`) |
| Config | `config/settings.py` |

**Known Gaps Raised.** Empirical threshold calibration deferred to **Phase 7B.5** (post-production data) — the open pipeline work.

---

## Phase 7C — Scraper Retirement (2026-05-09)

**Outcome.** Formal removal of the post-Silver scraper now made redundant by Phase 7A's full
body text: 5 files deleted, the `scrape_attempted` column dropped via migration `002`,
`newspaper4k` removed. 1860 tests green; `full_text_raw` retained.

**Key Decisions.**
1. Full deletion (Sprint 21.5 `news_producer.py` precedent), not deprecation.
2. Forward-only migration `002_drop_scrape_attempted.sql`; no rollback path.
3. `newspaper4k` removal mandatory; `requirements.lock` regenerated.

**Tasks Completed.**
| Deliverable | File |
|---|---|
| Deletions | `utils/article_scraper.py`, `orchestration/scraper_runner.py`, `dags/scraper_dag.py`, test + migration `001` |
| Schema drop | `infrastructure/sql/init.sql`, migration `002`, `k8s/postgres-configmap.yaml` |
| Dependency + docs | `requirements.txt`, `requirements.lock`, `docs/VALIDATION_GUIDE.md` |

**Known Gaps Raised.** None — the truncated-text gap is closed (accepted as the new normal). Migration `002` scheduled for cloud Postgres as part of Phase 9 (C5) preparation.
