# Phase 7 — Provider Migration + Intelligent Filtering Layer + Scraper Retirement
## Anizai Project | Phase 7 (Phase 7A + Phase 7B + Phase 7C)

---

## How to use this document

This is the **granular implementation plan** for Phase 7 of the Anizai data pipeline — provider migration to newsapi.ai (Event Registry), the Intelligent Filtering Layer, and Scraper Retirement.

It should be loaded by Claude Code at the start of every Phase 7 sprint, alongside:
- `CLAUDE.md` (engineering guardrails, skill reference)
- The relevant Phase 7 section in `task_plan.md` (active task tracker)
- The required skills: `sprint-kickoff`, `code-review`, `filter-analysis` (for Phase 7B)

The data-pipeline sprint conventions still apply:
- Conventional commits with section references
- Update `task_plan.md` after every completed task
- All work inside `data-pipeline/`
- No code without an approved implementation plan
- The four-gate testing model (Gate 1 / Gate 2 / Gate 3 / E2E)

---

## Phase 7 Overview

### Goal

Three sequential sprints that together upgrade the NewsAPI ingestion path from a snippet-based, single-layer keyword filter to a full-body, two-stage gate, and retire the now-redundant post-Silver scraper:

1. **Phase 7A — Provider Migration to newsapi.ai (Event Registry).** Replace TheNewsAPI (`api.thenewsapi.com`) with newsapi.ai (`eventregistry.org`). The producer is the API-shape boundary (Section 3.3): all changes confined to `ingestion/newsapi_producer.py` and its config/tests. Wire-protocol `source_name="newsapi"` and Kafka topic `BRONZE_NEWSAPI` are preserved — no schema migration, no downstream churn. The critical functional gain is the `body` field (full article text), replacing TheNewsAPI's 60-char `snippet`.
2. **Phase 7B — Filter Improvements + Semantic Rescue.** Replace the single-layer keyword filter (which over-filters with noisy terms like `"strike"`/`"attack"` and admits everything else above a 0.09 floor) with a two-stage gate: a tighter deterministic sniper plus an embedding-based semantic rescue for borderline articles. Calibration is performed against the full body now arriving from newsapi.ai — that data dependency is why Phase 7B follows 7A.
3. **Phase 7C — Scraper Retirement.** Delete the post-Silver web scraper (`scraper_runner.py`, `article_scraper.py`, `scraper_dag.py`), the corresponding tests, and the `scrape_attempted` column. Remove `newspaper4k`. Phase 7A already eliminates the *functional* role of the scraper (newsapi.ai's `body` field replaces what the scraper recovered); Phase 7C is the formal code/infra removal.

### Non-goals

- No change to Silver-layer DLQ contracts.
- No change to `process_*_gold_message()` signatures — semantic rescue (Phase 7B) lives in `GlobalNewsGoldFunction.process_element` only.
- No change to `BRONZE_NEWSAPI` Kafka topic name (Phase 7A) or `source_name="newsapi"` on the wire.
- No change to `knowledge_vault` schema in Phase 7A. Column drops (`scrape_attempted`) are confined to Phase 7C.
- No change to `knowledge_vault.full_text_raw` — the column remains essential (the agent reads it via [agent/agents/researcher.py:176](data-pipeline/agent/agents/researcher.py#L176) and [agent/tools/knowledge_tools.py:95](data-pipeline/agent/tools/knowledge_tools.py#L95)). Only `scrape_attempted` and the scraper's write path are retired.

### Sprint Structure

Phase 7 splits into **three sprints, executed sequentially**. All three sprints land **before Phase C5** (Cloud Deployment closeout) so the cloud Postgres arrives without `scrape_attempted` from day one and migration `002_drop_scrape_attempted.sql` is applied as part of C5 preparation rather than as a post-C5 cleanup.

| Sprint | Phase | Focus | Definition of Done |
|--------|-------|-------|---------------------|
| Phase 7A | Phase 7 | Provider Migration to newsapi.ai | Producer fetches from `eventregistry.org/api/v1/article/getArticles`. Bronze emission preserved. Full `body` field reaches `knowledge_vault.full_text_raw`. Gate 2 Silver / Gate 2 Gold / Gate 3 NewsAPI tests pass without modification (proves API-shape boundary held). E2E live run shows 6 articles flowing through with full body. |
| Phase 7B | Phase 7 | Filter Improvements + Semantic Rescue | Cleaner keyword list, raised threshold, embedding-based rescue layer live in Gold path. Drop/promote logs visible at INFO. Calibration performed on post-7A vault rows (full body). |
| Phase 7C | Phase 7 | Scraper Retirement | All scraper code deleted, `scrape_attempted` column dropped via migration `002`, `newspaper4k` removed from requirements, VALIDATION_GUIDE.md updated. |

### Why this order

Phase 7B's keyword_sniper improvements and semantic rescue are calibrated against article content. TheNewsAPI provides `snippet` (60 chars). newsapi.ai provides `body` (full article text). Calibrating thresholds against truncated content and then switching to full text would invalidate the calibration. Phase 7C's justification compounds — the scraper existed to compensate for missing full text; newsapi.ai eliminates that need entirely.

**Sprint numbering**: Phase 7 sprints carry the **"Phase 7A" / "Phase 7B" / "Phase 7C" labels only** — no numeric sprint identifiers — to avoid collision with hub Sprint 22 (Reactive Search, Phase 8D) and Phase C sprints C1–C5. Tasks use the `T7A.x` / `T7B.x` / `T7C.x` prefixed convention so commit messages and `task_plan.md` rows unambiguously identify the source sprint.

**Ordering vs. Phase C**: Phase 7 runs **in parallel with Phase C** — Phase 7 touches only local pipeline code (producer, sniper, gold_job, schema migration, tests). Phase C touches K8s manifests. Phase 7C must complete **before C5** so the cloud Postgres deploy reflects the dropped column. The Phase 7C migration `002_drop_scrape_attempted.sql` is applied to cloud Postgres as part of C5 preparation.

**Phase 7B kickoff gate**: Phase 7B does **not** start until ≥200 newsapi.ai-shaped rows exist in `knowledge_vault` (post-Phase-7A). T7B.1 / T7B.2 / T7B.9 all calibrate empirically against vault data, and that data must reflect the full-body shape that the new sniper/rescue is designed to score against. Calibrating against pre-7A truncated rows would produce stale thresholds.

---

## Settled Design Decisions

### Phase 7A — Provider Migration

| ID | Decision |
|---|---|
| **M1** | Single endpoint `https://eventregistry.org/api/v1/article/getArticles` for both pulse and backfill (merge `_fetch_top_headlines` + `_fetch_everything` → `_fetch_articles`). Backfill mode adds `dateStart`/`dateEnd`; pulse mode omits them. HTTPS in all environments — `apiKey` in the request, never sent over plaintext HTTP. |
| **M2** | Auth via `apiKey` (replaces `api_token`). New env var `NEWSAI_API_KEY` in [config/settings.py](data-pipeline/config/settings.py). |
| **M3** | Source filter: `sourceUri=<csv-of-domains>` — `AUTHORITY_WHITELIST` domain values unchanged, only the parameter name moves. `_passes_whitelist` reads `article.get("source", {}).get("uri")` instead of the bare `article.get("source")`. |
| **M4** | Category mapping (TheNewsAPI → newsapi.ai `categoryUri`): `business → news/Business`, `tech → news/Technology`, `health → news/Health`, `science → news/Science`, `general → news` (root). **NEW** `news/Politics` added as an explicit category (did not exist on TheNewsAPI; previously political content was captured via `general` + Keyword Sniper, which raised both noise and false positives in `general`). The `news` (root) category continues to receive the producer-level `GENERAL_KEYWORDS` pre-filter; `news/Politics` passes through without keyword pre-filter (same treatment as business/tech/health/science). |
| **M5** | Required request params for full text: `resultType=articles`, `articlesSortBy=date` (pulse only — `articlesSortBy` omitted for backfill so the API decides natural date-range pagination order), `articleBodyLen=-1`, `includeArticleBody=true`, `lang=eng` (ISO 639-3, replaces `language=en`). |
| **M6** | Pagination params: `articlesPage` + `articlesCount` replace `page` + `limit`. Default `NEWSAI_PAGE_SIZE=10` (env-overridable via `NEWSAI_PAGE_SIZE`). Last-page detection on `len(articles) < page_size or page * page_size >= total_results` — same logic as today. |
| **M7** | Field mapping in `_build_raw_payload` (newsapi.ai → internal `raw_payload`):  `body → content` (full text, not snippet); `image → url_to_image`; `source.uri → source.id` (after TLD-strip via existing `_domain_to_source_id`); `source.title → source.name` (display name). The existing `DOMAIN_DISPLAY_NAMES` lookup is **kept as a fallback** in case `source.title` is absent on a particular article — preserves the human-readable contract for outliers and for E2E backwards compatibility. `authors[] → author` (joined with `", "`; empty list → `""`). `dateTime → published_at` (ISO8601 pass-through, replacing `published_at` field name on the upstream side). |
| **M8** | One-time validation script at [data-pipeline/scripts/validate_newsai.py](data-pipeline/scripts/validate_newsai.py) — pure HTTP, no Kafka, no DB. One call per category (6 calls = 6 tokens against the 2,000-token free-tier cap). Asserts: `body` non-empty and >200 chars (or documented as truncated if free-tier-capped per OQ-4 below), `source.uri` populated, `authors[]` present, `dateTime` ISO8601-parseable. **Deleted in T7A.16 after E2E passes.** |
| **M9** | TheNewsAPI constants (`THE_NEWS_API_KEY`, `THE_NEWS_API_PAGE_SIZE`, `NEWSAPI_BASE_URL`, `TOP_HEADLINES_ENDPOINT`, `EVERYTHING_ENDPOINT`) and the `TIER_ONE_SOURCE_IDS` backwards-compat alias are all **removed** in the same sprint's cleanup task (T7A.16) — no straddling deprecation window. Token budget for the entire sprint is ≤50 tokens (well under the 2,000 free-tier cap). If the free tier truncates `body` despite `articleBodyLen=-1`, accept and document as a known gap (final value awaits paid-tier upgrade); do **not** stop or reassess. |

### Phase 7B — Filter Improvements + Semantic Rescue

| ID | Decision |
|---|---|
| **A1** | Remove from `MASTER_KEYWORD_LIST`: `strike`, `attack`, `odds`, `token`, `inference`, `default`, `revenue`, `vote`, `energy`, `defense`. Keep all compound forms (`missile defense`, `armed forces`, `energy crisis`, `nuclear energy`, `fusion energy`). Final removal list confirmed by sample analysis on **post-7A vault rows** (filter-analysis skill, T7B.1). |
| **A2** | Raise `DEFAULT_THRESHOLD` from 0.09 → 0.15. Confirmed by relevance-score distribution analysis on **post-7A** `knowledge_vault` rows (T7B.2). Note: the distribution shifts with full body — keyword matches per article rise vs. the snippet-era distribution — so the empirical 0.15 target may move up; T7B.2 picks the value from the post-7A distribution. |
| **A3** | Sync check on `GENERAL_KEYWORDS` in [ingestion/newsapi_producer.py:224-234](data-pipeline/ingestion/newsapi_producer.py#L224-L234). **Pre-confirmed**: zero overlap between A1 removal list and the 9 B.4 GENERAL_KEYWORDS — no producer change needed. The verification step still runs in T7B.3 as a guard against later drift. The GENERAL_KEYWORDS list itself is unchanged in Phase 7A; the question of trimming political terms (`nato`, `sanctions`, `interest rates`, `central bank`, `ai regulation`) now that `news/Politics` is its own category is **revisited in T7B.1** with empirical data. |
| **B1** | One-time reference-vector script at [processing/build_sniper_reference_vector.py](data-pipeline/processing/build_sniper_reference_vector.py): embeds every term in the post-A1 `MASTER_KEYWORD_LIST` with `text-embedding-3-small`, mean-pools to a single 1536-dim vector, saves to `processing/sniper_reference_vector.npy`. ~600 tokens, ~$0.000012 per regeneration. |
| **B2** | Semantic rescue gate added to `GlobalNewsGoldFunction.process_element` ([processing/gold_job.py:2658-2725](data-pipeline/processing/gold_job.py#L2658-L2725)). Reference vector loaded once in `open()`. **Behavior change**: low-signal articles that fail BOTH the sniper and the semantic-rescue threshold are **dropped entirely** (no `kv_archive`). This is a deliberate change from today's behavior, where every Silver doc is archived regardless of `is_high_signal`. See "Architectural Change Notice" below. |
| **B3** | `GOLD_SEMANTIC_RESCUE_THRESHOLD` value set after offline calibration on a vault sample (T7B.9). Initial proposed range: **0.30–0.40** (text-embedding-3-small distributions skew higher than ada-002; the conservative end of the design-session 0.25–0.35 hint). Note: post-7A documents are denser (full body), so the empirical optimum may shift up within this range. Final value selected as the point where rescued items have ≥80% precision in manual classification. |
| **B4** | Hard-fail at startup if `sniper_reference_vector.npy` is missing (`logger.error` + `raise FileNotFoundError`). Prevents a silent regression to keyword-only filtering. |
| **B5** | Promotions and drops both log at `INFO` (not DEBUG): `[gold/semantic_rescue] promoted url=... score=...` and `[gold/semantic_rescue] dropped url=... score=...`. Operators need both rates visible in production. |
| **B6** | `numpy==2.4.4` is already in [requirements.lock:52](data-pipeline/requirements.lock#L52) and runs in the Flink image — no Dockerfile change needed. |
| **B7** | Env var name: **`GOLD_SEMANTIC_RESCUE_THRESHOLD`** (Gold-side namespacing parity with other Gold-tier env vars). Defined in `config/settings.py` with a safe default; readable at runtime by `GlobalNewsGoldFunction.open()`. |

### Phase 7C — Scraper Retirement

| ID | Decision |
|---|---|
| **C1** | Retire and DELETE: [utils/article_scraper.py](data-pipeline/utils/article_scraper.py), [orchestration/scraper_runner.py](data-pipeline/orchestration/scraper_runner.py), [orchestration/dags/scraper_dag.py](data-pipeline/orchestration/dags/scraper_dag.py), [tests/test_processing/test_article_scraper.py](data-pipeline/tests/test_processing/test_article_scraper.py), [infrastructure/sql/migrations/001_add_scrape_attempted.sql](data-pipeline/infrastructure/sql/migrations/001_add_scrape_attempted.sql). Parity with the Sprint 21.5 deletion of `news_producer.py` (full removal, not deprecation). |
| **C2** | **Drop `scrape_attempted` column** from `knowledge_vault`. Justification: only `update_scraped_text()` writes to it; once that function is deleted, the column is permanent-FALSE garbage. Keep `full_text_raw` (essential for hub RAG drill-down). Forward-only migration via `infrastructure/sql/migrations/002_drop_scrape_attempted.sql`. |
| **C3** | No scraper Deployment exists in `infrastructure/k8s/` (verified — only `producers/polymarket-deployment.yaml` is present). No `scraper` service exists in [infrastructure/docker-compose.yml](data-pipeline/infrastructure/docker-compose.yml) (verified). C3 reduces to a no-op confirmation in tests. |
| **C4** | Update [docs/VALIDATION_GUIDE.md](data-pipeline/docs/VALIDATION_GUIDE.md) — 10 line references to `newsapi_scraper` / `scrape_attempted` removed or rewritten (lines 244, 262, 282, 284, 348, 356, 359, 363, 364, 618). |
| **C5** | Tests that seed `scrape_attempted` in [tests/fixtures/sprint19_vault_seed.sql:82](data-pipeline/tests/fixtures/sprint19_vault_seed.sql#L82) and assert on it in [tests/e2e/run_full_validation.py:110-125,316](data-pipeline/tests/e2e/run_full_validation.py#L110-L125) are updated to remove the column. |
| **C6** | **Mandatory** — Remove `newspaper4k` (and any transitive `newspaper3k` reference) from [requirements.txt:78](data-pipeline/requirements.txt#L78) and [requirements.lock:51](data-pipeline/requirements.lock#L51). Pre-confirmed: every remaining `newspaper` import lives inside the files deleted in C1; no other module pulls it. Regenerate `requirements.lock` per CLAUDE.md Section 4.4. |

### Architectural Change Notice — B2

Today, every Silver document — regardless of `is_high_signal` — is archived to `knowledge_vault` via `kv_archive(silver_doc)` BEFORE the dispatch to `process_*_gold_message()`. Low-signal docs land in the vault without Gold enrichment, which means the agent's vault-keyword fallback can still surface them.

In the new flow, articles that fail BOTH the sniper AND semantic rescue are **dropped entirely** — no `kv_archive`, no Gold. This is a behavior change: the vault becomes strictly higher-signal, but loses some breadth.

**Why we accept this**: the noisy-keyword removal in A1 plus a 0.30–0.40 cosine-similarity floor in B3 should drop only articles that are genuinely off-domain (lifestyle, sports, local crime). Anything ambiguously domain-adjacent will be rescued. The hub's reactive search (Phase 8D) is the catch-all for breadth.

**Mitigation**: the B5 INFO-level drop logs let us measure the drop rate in production. If it exceeds expectations during the first 48 hours after merge, the threshold can be relaxed without code redeploy via `GOLD_SEMANTIC_RESCUE_THRESHOLD` env override (B7).

---

## Phase 7 Gate Model

Phase 7 is a pipeline-side phase with no LangGraph nodes. The pipeline 4-gate model applies:

| Gate | Meaning in Phase 7 |
|------|---|
| **Gate 1** | Module imports and constants (e.g., `validate_newsai.py` runs as a standalone script; modified `newsapi_producer.py` imports cleanly with new constants; `keyword_sniper.py` imports cleanly with new constants; `knowledge_vault.py` imports without the deleted functions). |
| **Gate 2** | Pure-function logic with mocks: producer field mapping under newsapi.ai shape; scoring math under the new keyword list/threshold; semantic rescue logic with mocked OpenAI client; dispatch + drop semantics in `GlobalNewsGoldFunction.process_element`. No live DB or live API. |
| **Gate 3** | Persistence/round-trip: schema migration applied; existing seed fixtures updated; `kv_archive` not called on dropped articles (verifiable via mock); INFO logs visible in captured logs; Gate 3 NewsAPI persistence tests pass unchanged after Phase 7A migration (proves the API-shape boundary held). |
| **E2E** | Live newsapi.ai → Bronze → Silver → Gold → vault. Phase 7A: 6 articles fetched (1 per category) reach `knowledge_vault` with full `body` populating `full_text_raw`. Phase 7B: drop rates and Gold-write rates compared against pre-Phase-7B baseline. |

Each task is tagged with which gate(s) it must pass before being marked `[x]`.

---

## Phase 7A — Provider Migration to newsapi.ai

### Sprint scope

Migrate `ingestion/newsapi_producer.py` from `api.thenewsapi.com` to `eventregistry.org` (newsapi.ai). The producer is the API-shape boundary (Section 3.3): all changes are confined to the producer file plus its config and tests. Silver/Gold/persistence/Gate 2/Gate 3 contracts are untouched. Wire-protocol `source_name="newsapi"` and Kafka topic `BRONZE_NEWSAPI` are preserved — no schema migration, no downstream churn.

The end of Phase 7A deliverable: a live newsapi.ai E2E run shows 6 articles flowing through the pipeline, each carrying full `body` text in `knowledge_vault.full_text_raw`. Gate 2 Silver / Gate 2 Gold / Gate 3 NewsAPI tests run **unchanged** and pass — the proof point that the API-shape boundary held.

### Confirmed design decisions

- All M1–M9 from the Settled Design Decisions table apply.
- HTTPS endpoint everywhere (`https://eventregistry.org/api/v1/article/getArticles`) — `apiKey` never sent over plaintext.
- New explicit category `news/Politics` is added as a sibling to Business/Technology/Health/Science. The `news` (root) category continues to receive the GENERAL_KEYWORDS pre-filter.
- Validation script lives at `data-pipeline/scripts/validate_newsai.py` (consistent with existing `scripts/` layout — `submit_query.py`, `preflight_firestore.py`, `inventory_pending_queries.py`).
- TheNewsAPI constants and the `TIER_ONE_SOURCE_IDS` alias are removed in the same sprint — no straddling deprecation window.
- If `articleBodyLen=-1` is silently capped on the free tier, accept and document as a known gap; full body arrives once the account upgrades.
- GENERAL_KEYWORDS list is unchanged in Phase 7A. Trimming political terms is deferred to T7B.1 with empirical data.

### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| T7A.1 | Build [scripts/validate_newsai.py](data-pipeline/scripts/validate_newsai.py) per M8. CLI: `python scripts/validate_newsai.py --api-key <KEY>`. Makes one HTTPS GET per category (Business/Technology/Health/Science/Politics + news root = 6 calls). Prints response shape, article count, body length for first article, `source.uri`, `authors`, `dateTime`. Asserts: `body` non-empty and >200 chars (or logs a known-gap warning if free tier caps it); `source.uri` populated; `authors[]` present; `dateTime` ISO8601-parseable. No Kafka, no DB, no imports from `ingestion/` or `processing/`. Idempotent — safe to re-run. | Gate 1 | M8, §B.4 |
| T7A.2 | Run T7A.1 locally **with user permission** against the live newsapi.ai free tier. Capture output. Confirm: (a) `articleBodyLen=-1` returns full body (>200 chars/article) — if truncated, document as a known gap and proceed (M9); (b) `source.uri`, `source.title`, `authors[]`, `dateTime` all populated; (c) `news/Politics` returns Israeli/US political articles; (d) confirm the response envelope path `data["articles"]["results"]` and `data["articles"]["totalResults"]` match what T7A.5 will rely on. Total tokens consumed ≤6. | E2E | M8, §B.4 |
| T7A.3 | Add to [config/settings.py](data-pipeline/config/settings.py) (new section "newsapi.ai / Event Registry — Section B.4"): `NEWSAI_API_KEY = os.getenv("NEWSAI_API_KEY", "")`, `NEWSAI_PAGE_SIZE = int(os.getenv("NEWSAI_PAGE_SIZE", "10"))`, `NEWSAI_BASE_URL = "https://eventregistry.org/api/v1"`. Keep `THE_NEWS_API_KEY` / `THE_NEWS_API_PAGE_SIZE` until T7A.16 cleanup. | Gate 1 | M2, §9.1 |
| T7A.4 | Rewrite the constants block in [ingestion/newsapi_producer.py:104-134](data-pipeline/ingestion/newsapi_producer.py#L104-L134): replace `NEWSAPI_BASE_URL` / `TOP_HEADLINES_ENDPOINT` / `EVERYTHING_ENDPOINT` with a single `NEWSAI_GETARTICLES_URL = f"{NEWSAI_BASE_URL}/article/getArticles"`. Update `PULSE_CATEGORIES` to use `categoryUri` values: `["news/Business", "news/Technology", "news/Health", "news/Science", "news/Politics"]`. Update `GENERAL_CATEGORY = "news"` (root URI). Add `CATEGORY_LEGACY_TO_URI` mapping if useful for log readability. Update `PAGE_SIZE = NEWSAI_PAGE_SIZE`. Source name (`SOURCE_NAME = "newsapi"`) unchanged per non-goals. | Gate 2 | M1, M4, M6, §B.4 |
| T7A.5 | Replace `_fetch_top_headlines` and `_fetch_everything` with a single `_fetch_articles(category, page=1, date_start=None, date_end=None)` per M1/M5/M6. Param keys: `apiKey`, `categoryUri=category`, `sourceUri=self._whitelist_domains_param()`, `lang="eng"`, `articlesPage=page`, `articlesCount=PAGE_SIZE`, `resultType="articles"`, `articleBodyLen=-1`, `includeArticleBody=True`. Pulse mode (no date range) adds `articlesSortBy="date"`. When date range provided, add `dateStart=date_start`, `dateEnd=date_end`. Response parsing: `data["articles"]["results"]` and `data["articles"]["totalResults"]` (path verified in T7A.2). | Gate 2 | M1, M5, M6 |
| T7A.6 | Rewrite `_build_raw_payload` per M7. Field mapping table in the docstring updated. `_passes_whitelist` updated to read `article.get("source", {}).get("uri")`. Display-name resolution: prefer `article["source"]["title"]`; fall back to `DOMAIN_DISPLAY_NAMES` lookup; final fallback to the bare domain. Author: `", ".join(article.get("authors", []) or [])`. `published_at` reads from `dateTime`. | Gate 2 | M3, M7 |
| T7A.7 | Verify `_passes_keyword_sniper` and `_impact_boost_info` continue to read from `title` and `description` fields — newsapi.ai preserves these field names per T7A.2 validation. **No code change expected.** If T7A.2 reveals different field names, this task triggers an update in the same commit. GENERAL_KEYWORDS list unchanged in 7A (deferred to T7B.1). | Gate 2 | A3, §B.4 |
| T7A.8 | Update `run_pulse` to iterate over the 5 explicit `PULSE_CATEGORIES` (`news/Business`, `news/Technology`, `news/Health`, `news/Science`, `news/Politics`) plus the `news` root category (which alone runs the GENERAL_KEYWORDS pre-filter). Update logging strings to show the URI rather than the legacy slug. `_emit` endpoint URL updated to `f"{NEWSAI_GETARTICLES_URL}?categoryUri={raw_payload['category']}"`. Update `run_backfill` callsites accordingly (still calls `_fetch_articles` with `date_start`/`date_end`). | Gate 2 | M4, M6 |
| T7A.9 | Rewrite module docstring at [ingestion/newsapi_producer.py:1-84](data-pipeline/ingestion/newsapi_producer.py#L1-L84): replace all "TheNewsAPI" / "thenewsapi.com" references with "newsapi.ai (Event Registry)" / "eventregistry.org". Replace the Sprint 21.5 migration paragraph with a Phase 7A migration paragraph (same structure: why migrating — 60-char snippet → full body; what stays stable — wire-protocol `source_name`, Kafka topic). Update inline comments throughout the file referencing `snippet`/`data[]`/`api_token`/`/v1/news/top`/`/v1/news/all`. Update the field-mapping table in `_build_raw_payload` docstring. | — | §3.3, §B.4 |
| T7A.10 | Update [orchestration/dags/newsapi_dag.py](data-pipeline/orchestration/dags/newsapi_dag.py) docstring lines 1-28: replace "TheNewsAPI" / "newsapi.org" wording with "newsapi.ai (Event Registry)". Code unchanged — `main(mode="pulse")` signature is preserved. | — | §6A |
| T7A.11 | Replace [tests/mocks/newsapi_article.json](data-pipeline/tests/mocks/newsapi_article.json) with a newsapi.ai-shaped fixture: top-level envelope `{"articles": {"results": [...], "totalResults": N}}`; per-article fields `title`, `body`, `description`, `image`, `url`, `dateTime`, `source: {"uri": "...", "title": "..."}`, `authors: [...]`. Fixture should include ≥1 Israel/Hamas article so impact_boost path is exercised, and ≥1 article from each tested category. | Gate 1/2 | M11, §9.3 Gate 2 |
| T7A.12 | Update [tests/test_ingestion/test_newsapi_gate1.py](data-pipeline/tests/test_ingestion/test_newsapi_gate1.py): assert producer reads from new field shape; assert new fixture envelope; assert `apiKey` (not `api_token`) is in request params; assert `articleBodyLen=-1`, `includeArticleBody=True`, `resultType="articles"`, `lang="eng"`, `articlesPage`, `articlesCount` are all present; assert `news/Politics` is in `PULSE_CATEGORIES`; assert `_build_raw_payload` produces the same internal `raw_payload` contract Silver expects (article_id from url; content from body; source.id from `_domain_to_source_id(source.uri)`; author from joined `authors[]`). Mock `requests.get` to return the new fixture shape. | Gate 2 | §9.3 Gate 2 |
| T7A.13 | Run Gate 2 Silver, Gate 2 Gold, and Gate 3 NewsAPI test suites **without modification** to confirm the API-shape boundary held. Document the run output in the commit message. If any test fails, the defect is in T7A.6's mapping — diagnose and fix in the producer, not in downstream tests. | Gate 2/3 | §9.3, §3.3 |
| T7A.14 | E2E live run **with user permission**: set `NEWSAI_API_KEY` in `.env`, update [tests/e2e/run_newsapi_e2e.py](data-pipeline/tests/e2e/run_newsapi_e2e.py) fixture-validation block (if any) to accept newsapi.ai response shape, run the script. Expected: 6 articles fetched (1 per pulse category — Business/Technology/Health/Science/Politics + `news` root after GENERAL_KEYWORDS pre-filter), all 6 reach Bronze/Silver/Gold, body field is full text (≥200 chars/article — or documented as truncated per M9), `IMPACT BOOSTED +1` triggers on Israel/ME articles. Token usage ≤6 (well under cap). Record before/after metrics in commit message. | E2E | §9.3 E2E |
| T7A.15 | Update [docs/VALIDATION_GUIDE.md](data-pipeline/docs/VALIDATION_GUIDE.md): replace TheNewsAPI / thenewsapi.com references with newsapi.ai / eventregistry.org. **Scraper-related entries in the same file are deferred to Phase 7C T7C.7.** | — | §6A |
| T7A.16 | Cleanup. Delete [scripts/validate_newsai.py](data-pipeline/scripts/validate_newsai.py); remove `THE_NEWS_API_KEY` and `THE_NEWS_API_PAGE_SIZE` from [config/settings.py](data-pipeline/config/settings.py); remove the `TIER_ONE_SOURCE_IDS` backwards-compat alias from [ingestion/newsapi_producer.py:894](data-pipeline/ingestion/newsapi_producer.py#L894); remove any orphaned legacy constants. Verify with grep that zero callers of the removed names remain across `data-pipeline/`. Update `.env.example` if present. | — | M9, §3.2 DRY |
| T7A.17 | Update [task_plan.md](data-pipeline/task_plan.md): close Phase 7A row, mark Phase 7B as queued (not active — Phase 7B does not start until ≥200 newsapi.ai-shaped rows exist in `knowledge_vault`). | — | — |

### Constants introduced (Phase 7A)

- `NEWSAI_API_KEY`, `NEWSAI_PAGE_SIZE`, `NEWSAI_BASE_URL` in `config/settings.py`
- `NEWSAI_GETARTICLES_URL` in `ingestion/newsapi_producer.py`
- `CATEGORY_LEGACY_TO_URI` mapping (optional — for log readability) in `ingestion/newsapi_producer.py`

### Constants removed (Phase 7A, T7A.16 cleanup)

- `THE_NEWS_API_KEY`, `THE_NEWS_API_PAGE_SIZE` in `config/settings.py`
- `NEWSAPI_BASE_URL`, `TOP_HEADLINES_ENDPOINT`, `EVERYTHING_ENDPOINT` in `ingestion/newsapi_producer.py`
- `TIER_ONE_SOURCE_IDS` backwards-compat alias

### DLQ paths (Phase 7A)

No new DLQ routes. Existing Bronze→Silver validation guards (Section 4.1C) still apply unchanged: malformed payloads → `dead-letter-queue`. The producer-level keyword sniper / whitelist gates remain pre-Bronze rejections (no DLQ — never entered the pipeline).

### Acceptance criteria — Phase 7A

- All Gate 1, Gate 2, and Gate 3 NewsAPI test suites pass — Gate 2 Silver / Gate 2 Gold / Gate 3 pass **without source-code modification** (proof the API-shape boundary held).
- E2E live run shows full body in `knowledge_vault.full_text_raw` (or documented free-tier truncation) and 6 articles flowing through.
- Zero remaining references to `thenewsapi`, `api_token`, `published_after`, `image_url`, `snippet` (as a NewsAPI-field name), or `data[]` (in NewsAPI context) in `data-pipeline/` (verifiable by grep, scoped to ingestion/processing/persistence/tests).
- `requirements.lock` unchanged (no new pip deps; standard `requests` is sufficient).
- `task_plan.md` updated with closeout row.

### Open questions deferred to future sprints

- None for Phase 7A. The free-tier body-truncation question (M9) is accept-and-document, not deferred.

---

## Phase 7B — Filter Improvements + Semantic Rescue

### Sprint kickoff gate

**Phase 7B does not start until ≥200 newsapi.ai-shaped rows exist in `knowledge_vault`** (post-Phase-7A). T7B.1 / T7B.2 / T7B.9 all calibrate empirically against vault data, and that data must reflect the full-body shape that the new sniper/rescue is designed to score against.

### Sprint scope

Land the cleaner deterministic sniper (A1+A2+A3) and the embedding-based semantic rescue layer (B1–B7). At end of Phase 7B, the Gold path's `GlobalNewsGoldFunction.process_element` runs the new two-stage gate against every Silver doc. Articles that pass either stage are archived and Gold-enriched; articles that fail both are logged-and-dropped.

The end of Phase 7B deliverable: a live newsapi.ai E2E run shows the new drop/rescue behavior with INFO-level logs visible, drop rate within expected bounds (~5–15% additional articles dropped at the new sniper threshold; some of those rescued via semantic), total Gold writes ≥80% of pre-Phase-7B baseline.

### Confirmed design decisions

- All A1–A3, B1–B7 from the Settled Design Decisions table apply.
- Calibration sample for T7B.1 / T7B.2 / T7B.9 is drawn from **post-7A vault rows only**. Mixing in pre-7A snippet-era rows would produce stale thresholds.
- Reference vector lives at `processing/sniper_reference_vector.npy` and is **committed to the repo** (binary, ~12 KB). Regenerated via `python processing/build_sniper_reference_vector.py` whenever `MASTER_KEYWORD_LIST` materially changes.
- `GOLD_SEMANTIC_RESCUE_THRESHOLD` is env-driven via `config/settings.py`, with the calibrated default chosen in T7B.9 baked into source.
- Semantic rescue mutates `silver_doc["is_high_signal"]=True` in-place when promoting. The downstream `process_*_gold_message()` functions need no signature change.
- Embedding API failures during rescue route to **DLQ** (not silent skip, not silent drop). Tested in T7B.10.
- T7B.1's filter analysis is also the empirical decision point for trimming political terms (`nato`, `sanctions`, `interest rates`, `central bank`, `ai regulation`) from `GENERAL_KEYWORDS` now that `news/Politics` is its own category in Phase 7A.

### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| T7B.1 | Run `filter-analysis` skill against ≥200 **post-Phase-7A** `knowledge_vault` rows (full body content). Classify TP/TN/FP/FN for each A1 candidate term. Confirm or trim the removal list. Also evaluate trimming political terms (`nato`, `sanctions`, `interest rates`, `central bank`, `ai regulation`) from `GENERAL_KEYWORDS` now that `news/Politics` is its own category. Save analysis report to `docs/phase7_filter_analysis.md`. | — | §4.1A, filter-analysis skill |
| T7B.2 | Threshold calibration. Pull `relevance_score` distribution from `knowledge_vault` (post-Phase-7A rows produced under threshold=0.09 against full body). Compute the percentile that 0.15 corresponds to under the new distribution. Confirm 0.15 doesn't drop legitimate high-signal articles, or pick the empirical replacement. Append findings to the analysis report. | — | §4.1A |
| T7B.3 | Apply A1 removals to `MASTER_KEYWORD_LIST` in [processing/keyword_sniper.py:98-323](data-pipeline/processing/keyword_sniper.py#L98-L323). Apply A2 (`DEFAULT_THRESHOLD = 0.15` at line 78, or the T7B.2-empirical value). Verify A3 — no overlap with `GENERAL_KEYWORDS` (already confirmed; reassert in commit message). If T7B.1 recommends GENERAL_KEYWORDS edits, apply in [ingestion/newsapi_producer.py:224-234](data-pipeline/ingestion/newsapi_producer.py#L224-L234) in the same commit. | Gate 2 | §4.1A, §B.4 |
| T7B.4 | Update [tests/test_processing/test_keyword_sniper.py](data-pipeline/tests/test_processing/test_keyword_sniper.py): adjust any test that depended on a removed keyword (none expected — quick scan; `B4_REQUIRED_KEYWORDS` at line 61 is unaffected). Add tests asserting A1 removed terms are NOT in `MASTER_KEYWORD_LIST`. Re-confirm `test_default_threshold_in_range` at line 550 still passes with the T7B.2 value. | Gate 2 | §9.3 Gate 2 |
| T7B.5 | Build [processing/build_sniper_reference_vector.py](data-pipeline/processing/build_sniper_reference_vector.py). CLI usage: `python processing/build_sniper_reference_vector.py`. Outputs `processing/sniper_reference_vector.npy`, prints token usage, prints SHA256 of the npy and a SHA256 of the current keyword set for traceability. Idempotent (safe to re-run). Docstring references §4.1A and Phase 7 plan. | Gate 1 | §4.1A |
| T7B.6 | Run `build_sniper_reference_vector.py` once locally; commit `processing/sniper_reference_vector.npy`. Document SHA256 of the npy in the commit message for audit. | E2E | §4.1A |
| T7B.7 | Add semantic rescue to `GlobalNewsGoldFunction`. Changes to [processing/gold_job.py:2653-2725](data-pipeline/processing/gold_job.py#L2653-L2725): (a) `open()` loads `sniper_reference_vector.npy` into `self._sniper_ref_vec` (`np.load`, fail-loud per B4); (b) `process_element` checks `is_high_signal`; if False, runs semantic rescue; if rescued, sets `silver_doc["is_high_signal"]=True` and continues; if not rescued, logs INFO drop + returns (no `kv_archive`). Reads `GOLD_SEMANTIC_RESCUE_THRESHOLD` from settings. | Gate 2 | §4.1A, §4.2A |
| T7B.8 | Add `GOLD_SEMANTIC_RESCUE_THRESHOLD` constant in [config/settings.py](data-pipeline/config/settings.py) (env-driven, default = value chosen in T7B.9 calibration). Document in `.env.example` if present. | — | §4.2A |
| T7B.9 | Calibration task — run T7B.7 logic against a sample of 100 known-low-signal **post-Phase-7A** articles (sniper rejected) from `knowledge_vault`. Manually classify rescued vs. dropped. Pick the `GOLD_SEMANTIC_RESCUE_THRESHOLD` that gives ≥80% precision on rescued items. Bake the chosen value into `config/settings.py` default and append findings to `docs/phase7_filter_analysis.md`. | — | filter-analysis skill |
| T7B.10 | Gate 2 unit tests for semantic rescue. New file `tests/test_processing/test_semantic_rescue.py`. Tests: (a) ref-vector load fails loudly when file missing; (b) rescue path sets `is_high_signal=True` and continues; (c) drop path skips `kv_archive` and `kv_insert`; (d) drop logs at INFO level; (e) embedding API failure → DLQ (NOT silent skip). Mock `OpenAI.embeddings.create` and the file load. | Gate 2 | §9.3 Gate 2 |
| T7B.11 | Gate 3 — emulate full `GlobalNewsGoldFunction.process_element` against a mocked Flink runtime context with the real `.npy` and a mocked OpenAI client. Verify metric: of N test silver_docs (mix of high/low/borderline), the dispatch counts (kv_archive called, kv_insert called, dropped) match expectations. | Gate 3 | §9.3 Gate 3 |
| T7B.12 | Update sprint state in `task_plan.md`. Mark Phase 7B row active. | — | — |
| T7B.13 | E2E validation — run [tests/e2e/run_newsapi_e2e.py](data-pipeline/tests/e2e/run_newsapi_e2e.py) end-to-end against a live newsapi.ai fetch. Expect: same article volume reaches Bronze; ~5–15% additional articles dropped at the new sniper threshold; some of those rescued via semantic; total Gold writes ≥80% of pre-Phase-7B baseline. Record before/after metrics in commit message. | E2E | §9.3 E2E |

### Constants introduced (Phase 7B)

- `DEFAULT_THRESHOLD = 0.15` (or T7B.2-empirical value, was 0.09) in `processing/keyword_sniper.py`
- `GOLD_SEMANTIC_RESCUE_THRESHOLD` in `config/settings.py`, env-overridable, initial value from T7B.9
- `SNIPER_REFERENCE_VECTOR_PATH` (path constant for the `.npy` file, used by both `build_sniper_reference_vector.py` and `gold_job.py`)

### DLQ paths (Phase 7B)

| Failure | Routes to | Reason |
|---|---|---|
| `sniper_reference_vector.npy` missing at `open()` | Worker startup `raise` (fail loud) | Per B4 — silent skip would regress to keyword-only filtering. |
| OpenAI embedding API call fails during semantic rescue | `dead-letter-queue` topic | Cannot make a sound rescue/drop decision without the embedding; CLAUDE.md §3.5 forbids silent drops. |
| Silver doc fails sniper AND fails semantic rescue (similarity < threshold) | Dropped (no DLQ, INFO log only) | Intentional design — these are off-domain articles, not validation failures. |

### Acceptance criteria — Phase 7B

- All Gate 2 unit tests pass (existing sniper tests + new semantic-rescue tests + amended Global News function tests).
- E2E run shows: drops logged at INFO, promotions logged at INFO, vault dedup unchanged, no DLQ regression.
- `task_plan.md` updated with closeout row.
- Phase 7B entries removed from "Deferred Optimizations" of `task_plan.md`.

### Open questions deferred to future sprints

- None for Phase 7B.

---

## Phase 7C — Scraper Retirement

### Sprint scope

**Phase 7A's migration to newsapi.ai already eliminates the *functional* need for the scraper** — the `body` field provides full article text at Bronze time. Phase 7C is the formal removal: code, schema column, dependency, fixtures, docs.

Pure removal sprint with one schema migration. Delete the post-Silver web scraper (`scraper_runner.py`, `article_scraper.py`, `scraper_dag.py`, the corresponding test file, and the `001_add_scrape_attempted.sql` migration). Drop the `scrape_attempted` column from `knowledge_vault` via a new forward-only migration `002_drop_scrape_attempted.sql`. Remove `newspaper4k` from `requirements.txt` and `requirements.lock`. Update `VALIDATION_GUIDE.md` and seed fixtures.

The end of Phase 7C deliverable: `git status` shows the deletions, `\d knowledge_vault` (against local Postgres) shows no `scrape_attempted` column, and `pytest` collection runs to completion with no import errors. The cloud Postgres receives `002_drop_scrape_attempted.sql` as part of Phase C5 preparation.

### Confirmed design decisions

- All C1–C6 from the Settled Design Decisions table apply.
- `full_text_raw` column is **kept**. It is the agent's RAG drill-down source ([agent/agents/researcher.py:176](data-pipeline/agent/agents/researcher.py#L176), [agent/tools/knowledge_tools.py:95](data-pipeline/agent/tools/knowledge_tools.py#L95)). The trigram GIN index `idx_kv_fulltext_trgm` is also kept.
- Migration is forward-only. No rollback path. A revival of scraping would be a new Phase that adds a clean column from spec.
- `newspaper4k` removal is **mandatory** (C6). Pre-confirmed: every `newspaper` import lives inside files we're deleting in C1.

### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| T7C.1 | Confirm zero remaining callers of `fetch_unscraped_articles` and `update_scraped_text` outside their definition site. (Pre-confirmed in file review: only `scraper_runner.py` calls them.) | — | §3.2 DRY |
| T7C.2 | Delete: [utils/article_scraper.py](data-pipeline/utils/article_scraper.py), [orchestration/scraper_runner.py](data-pipeline/orchestration/scraper_runner.py), [orchestration/dags/scraper_dag.py](data-pipeline/orchestration/dags/scraper_dag.py), [tests/test_processing/test_article_scraper.py](data-pipeline/tests/test_processing/test_article_scraper.py), [infrastructure/sql/migrations/001_add_scrape_attempted.sql](data-pipeline/infrastructure/sql/migrations/001_add_scrape_attempted.sql). | — | §3.2 DRY |
| T7C.3 | Remove from [persistence/knowledge_vault.py](data-pipeline/persistence/knowledge_vault.py): `fetch_unscraped_articles()` (lines 305-334), `update_scraped_text()` (lines 337-366), and the "Scraping layer — Sprint 15" section header. Update the module docstring (lines 25-26) to drop the two functions. Verify no other test references break. Remove the `newspaper4k` mention from line 349's docstring. | Gate 1 | §3.2 DRY |
| T7C.4 | Drop `scrape_attempted` column from `knowledge_vault`. Edit [infrastructure/sql/init.sql](data-pipeline/infrastructure/sql/init.sql) lines 80-83 (remove the column and the comment block). Edit [infrastructure/k8s/postgres-configmap.yaml](data-pipeline/infrastructure/k8s/postgres-configmap.yaml) lines 111-114 to match. Create new `infrastructure/sql/migrations/002_drop_scrape_attempted.sql` for live DBs (`ALTER TABLE knowledge_vault DROP COLUMN IF EXISTS scrape_attempted;`). | Gate 3 | §5.1 |
| T7C.5 | Apply migration 002 to local dev Postgres (when user runs it after approval). Restart Flink Silver/Gold jobs. | E2E | §5.1 |
| T7C.6 | Update [tests/fixtures/sprint19_vault_seed.sql](data-pipeline/tests/fixtures/sprint19_vault_seed.sql) — drop `scrape_attempted` from line 82 INSERT column list and from VALUES tuples. Update [tests/e2e/run_full_validation.py](data-pipeline/tests/e2e/run_full_validation.py) lines 110-125 and 316 — remove the `scrape_attempted` query block and the `scraped` summary line. | Gate 3 | §9.3 Gate 3 |
| T7C.7 | Update [docs/VALIDATION_GUIDE.md](data-pipeline/docs/VALIDATION_GUIDE.md) — remove or rewrite all 10 references to `newsapi_scraper` / `scrape_attempted` / `scrape` (lines 244, 262, 282-284, 348, 356-364, 618). Confirm the DAG list at line 244 drops the row. | — | §6A |
| T7C.8 | Confirm cloud manifest cleanup: no scraper Deployment in `infrastructure/k8s/` (verified — only `producers/polymarket-deployment.yaml` exists), no scraper service in `infrastructure/docker-compose.yml` (verified — single match is a Prometheus config comment). Add commit-message note. | — | §8.10 |
| T7C.9 | Remove `newspaper4k` from [requirements.txt:78](data-pipeline/requirements.txt#L78) and [requirements.lock:51](data-pipeline/requirements.lock#L51). Per C6, this is **mandatory**. Verify with grep that zero remaining `newspaper` imports exist outside the deleted files. Regenerate `requirements.lock` per CLAUDE.md Section 4.4 (clean rebuild + `pip freeze`). Commit both files together. | Gate 1 | §4.4 |
| T7C.10 | Run full pipeline test suite. Confirm: no test references the deleted modules; Gate 2/3 NewsAPI tests still pass; `run_newsapi_e2e.py` succeeds. | E2E | §9.3 |
| T7C.11 | Schedule migration `002_drop_scrape_attempted.sql` for cloud Postgres as part of Phase C5 preparation. Add to the Phase C5 sprint kickoff checklist. | — | Phase C5 |
| T7C.12 | Update `task_plan.md` — close Phase 7C row, add to archive. Remove "Gold embeddings based on truncated API text" gap from Known Gaps (Phase 7A's full-body migration plus the scraper exit means the gap is no longer "deferred", it's the new normal — accepted). | — | — |

### DLQ paths (Phase 7C)

None — Phase 7C is pure deletion + schema migration. No new DLQ routes.

### Acceptance criteria — Phase 7C

- All deleted files verified gone via `git status`.
- `tests/` collection completes with no import errors (verifies T7C.3 and T7C.6 cleaned everything).
- Local Postgres: `\d knowledge_vault` shows no `scrape_attempted` column.
- VALIDATION_GUIDE.md no longer mentions the scraper DAG.
- `requirements.lock` no longer contains `newspaper4k`.
- Phase C5 kickoff checklist includes migration `002_drop_scrape_attempted.sql`.
- Phase C deployment plan unaffected (scraper was not in any K8s manifest pre-Phase-C anyway).

### Open questions deferred to future sprints

- None.

---

## Phase 7 Risks (carried into all three sprints)

1. **Vault breadth shrinks unexpectedly (Phase 7B).** The new drop-instead-of-archive semantics could remove articles the agent's reactive-search fallback was implicitly relying on for breadth. *Mitigation*: B5 INFO logs + the 48-hour observation window + env-driven `GOLD_SEMANTIC_RESCUE_THRESHOLD` for fast rollback.
2. **Reference vector drift (Phase 7B).** If `MASTER_KEYWORD_LIST` is later edited without re-running T7B.5, the vector will be stale and semantic rescue will silently degrade. *Mitigation*: T7B.5 prints the SHA256 of the current keyword set; a top-of-file comment in `keyword_sniper.py` flags the regen requirement.
3. **`text-embedding-3-small` API failures during Gold processing (Phase 7B).** New external dependency on the rescue path. *Mitigation*: T7B.10 explicitly tests "embedding API failure → DLQ" — we route to DLQ rather than silently letting the article through or silently dropping it.
4. **Cloud Postgres migration timing (Phase 7C).** Migration `002` must be applied as part of the next cloud deploy before `init.sql` parity is restored. *Mitigation*: T7C.11 schedules this for Phase C5 preparation.
5. **Free-tier body truncation (Phase 7A, NEW).** newsapi.ai's free tier may silently cap `body` length despite `articleBodyLen=-1`. *Mitigation*: T7A.2 measures body length on every category; if capped, document as a known gap (per M9), accept current length as an upper bound, and plan the paid-tier upgrade as a follow-up. Phase 7B's calibration must then re-run after the upgrade.
6. **API-shape drift on newsapi.ai (Phase 7A, NEW).** If newsapi.ai changes the response envelope path (`data["articles"]["results"]` vs. `data["articles"]` directly) or rename a field between T7A.2 validation and T7A.14 E2E, the producer breaks. *Mitigation*: T7A.2 captures the exact envelope path; T7A.5 hard-codes that path with a clear constant; T7A.14 re-runs the validation script as a smoke test before the live E2E.

---

## Definition of Done — Phase 7

- [x] **Phase 7A merged** (2026-05-09): producer migrated to newsapi.ai, full `body` reaching `knowledge_vault.full_text_raw`, Gate 2/3 NewsAPI tests pass unchanged, E2E live run succeeds (45/45 articles, 0 DLQ), all TheNewsAPI constants removed.
- [x] **Phase 7B merged** (2026-05-09): keyword list cleaned (10 A1 removals), threshold raised 0.09→0.15, semantic rescue live (`compute_semantic_rescue()` + `GlobalNewsGoldFunction` two-stage gate), `sniper_reference_vector.npy` committed, 238 tests green. Calibration deferred to **Phase 7B.5** (post-C4 production data — empirical basis against pre-production rows would be stale).
- [x] **Phase 7C merged** (2026-05-09): scraper code deleted (5 files), `scrape_attempted` column dropped via migration `002` (applied locally; C5.0 cloud task scheduled), `newspaper4k` removed from requirements, VALIDATION_GUIDE.md updated, 1860 tests green.
- [x] `task_plan.md`: Phase 7 rows closed 2026-05-09. Phase 7B.5 queued as a future sprint (opens after Phase C4 is live).
- [x] One E2E newsapi.ai run shows articles flowing through with full body (T7A.14: 45 articles, 35 Gold writes, 147s wall-clock).
- [ ] `docs/phase7_filter_analysis.md` — **deferred to Phase 7B.5**. Will be committed after empirical calibration against post-C4 production vault rows (A1 removals and A2/B3 thresholds were set theoretically in Phase 7B; Phase 7B.5 validates them with real data).
- [x] Migration `002_drop_scrape_attempted.sql` scheduled in Phase C5 kickoff checklist (added as pre-task C5.0).
