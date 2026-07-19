# Sprint 19 — Persistence-API Audit (T19.0)

**Author:** Senior Data Engineer (Claude)
**Date:** 2026-04-30
**Sprint:** 19 (Phase 8B, part 1 of 2)
**Status:** Awaiting approval — gates T19.1.

---

## 1. Purpose

Pre-flight verification that every `persistence/*` call required by the three Sprint 19 retrieval agents (§8.4.1 Researcher, §8.4.2 Pulse Analyst, §8.4.3 Market Bridge) **already exists today** with a compatible signature. Drift between the spec call style and the existing function is recorded so the `agent/tools/` wrappers (T19.1) normalize at one boundary instead of every call site.

T19.1 cannot start until this report is approved. No code lands in `agent/tools/`, `agent/agents/`, or `agent/nodes/` before approval (D7).

## 2. Scope

Modules audited (read in full):

| Module | Path | Audited functions |
|---|---|---|
| Knowledge vectors | [data-pipeline/persistence/knowledge_vectors.py](../persistence/knowledge_vectors.py) | `similarity_search`, `fetch_by_signal_id`, `fetch_by_canonical_event` |
| Knowledge vault | [data-pipeline/persistence/knowledge_vault.py](../persistence/knowledge_vault.py) | `fetch_by_doc_id`, `fetch_by_canonical_event` |
| Social vectors | [data-pipeline/persistence/social_vectors.py](../persistence/social_vectors.py) | `similarity_search`, `fetch_by_signal_id` |
| Social vault | [data-pipeline/persistence/social_vault.py](../persistence/social_vault.py) | `fetch_by_social_id`, `fetch_by_canonical_event` |
| Momentum vault | [data-pipeline/persistence/momentum_vault.py](../persistence/momentum_vault.py) | `fetch_latest`, `fetch_time_series`, `fetch_fred_anomalies`, `fetch_fred_series` |
| Mapping dict | [data-pipeline/persistence/mapping_dict.py](../persistence/mapping_dict.py) | `lookup_by_canonical`, `lookup_by_platform_id` |

Modules **not** in scope for Sprint 19: `mapping_dict.find_similar_and_link` (Phase 7); insert/archive paths on every module (Sprint 19 is read-only against the vaults).

---

## 3. Call mapping — per agent

### 3.1 Researcher (§8.4.1)

| Step | Spec call | Existing function | Drift | Status |
|---|---|---|---|---|
| 1 | `knowledge_vectors.similarity_search(query_embedding, limit=15, min_impact_level=2, min_reliability=0.3)` | `knowledge_vectors.similarity_search(query_vector, limit=10, source_platform=None, min_impact_level=None, min_reliability=None)` ([knowledge_vectors.py:343-424](../persistence/knowledge_vectors.py#L343-L424)) | Param renamed `query_embedding`→`query_vector` (cosmetic). Defaults for `limit`, `min_impact_level`, `min_reliability` differ — wrapper passes the spec values explicitly. | OK |
| 2 | Composite ranking 0.6·sim + 0.25·impact_norm + 0.15·recency | n/a (agent-side computation, not persistence) | — | OK |
| 3 | Drill-down to `knowledge_vault` via `silver_data_ref` (top 5) | `knowledge_vault.fetch_by_doc_id(doc_id)` ([knowledge_vault.py:192-231](../persistence/knowledge_vault.py#L192-L231)) | The vector row's `silver_data_ref::text` is the `doc_id` UUID — pass through directly. **Correction (T19.2, 2026-04-30):** original audit incorrectly assumed `knowledge_vectors.similarity_search` returned `silver_data_ref` in its row dict. It did not — the column was missing from the SELECT, breaking parity with `social_vectors.similarity_search` (which has always exposed it). T19.2 restored parity by adding `kv.silver_data_ref::text` to the SELECT in [persistence/knowledge_vectors.py:401-419](../persistence/knowledge_vectors.py#L401-L419) and added a regression assertion in [tests/test_agent/test_tools_smoke.py](../tests/test_agent/test_tools_smoke.py). | OK (post-correction) |
| 4 | Tag `evidence_weight` per item | n/a (agent-side) | — | OK |
| 5 | Emit `vault_query_result` agentEvents | **deferred — D2 (Sprint 25)** | not invoked this sprint | OK |
| 6 | Return `ResearcherEvidence` dict | n/a (agent-side packaging) | — | OK |

**Returned fields available from `similarity_search`** match every key the agent needs:
`signal_id`, `canonical_event_id`, `source_platform`, `published_at`, `content_vitals` (→ `title`, `url`), `enrichment_ai` (→ `executive_summary`, `key_findings`, `impact_level`, `reliability_score`, `sentiment_score`), `domain_context`, `similarity`. Similarity field name = `similarity` (consistent with §3.2 below).

### 3.2 Pulse Analyst (§8.4.2)

| Step | Spec call | Existing function | Drift | Status |
|---|---|---|---|---|
| 1 | `social_vectors.similarity_search(query_embedding, limit=10)` | `social_vectors.similarity_search(query_vector, limit=10, min_impact=1, platforms=None, min_reliability=0.0)` ([social_vectors.py:255-339](../persistence/social_vectors.py#L255-L339)) | **DRIFT vs. knowledge_vectors:** (a) param name `min_impact` (int, default 1, "1 disables") vs. knowledge's `min_impact_level` (Optional[int]); (b) param name `platforms` (list, default None) vs. knowledge's `source_platform` (single str); (c) return-key `similarity_score` vs. knowledge's `similarity`. | OK — wrappers normalize |
| 2 | Separate by platform (Polymarket vs HackerNews) | n/a (agent-side filter on `source_platform`) | — | OK |
| 3 | Polymarket: extract from `platform_logic` JSONB | row carries `platform_logic` column | — | OK |
| 4 | HackerNews: extract from `platform_logic` JSONB | row carries `platform_logic` column | — | OK |
| 5 | Consensus-extreme drill-down via `silver_data_ref` to `social_vault` | `social_vault.fetch_by_social_id(social_id)` ([social_vault.py:201-230](../persistence/social_vault.py#L201-L230)) | The vector row's `silver_data_ref` UUID is the `social_id`. Pass through. | OK |
| 6 | Tag `evidence_weight` per item | n/a (agent-side) | — | OK |
| 7 | Emit `vault_query_result` agentEvents | **deferred — D2 (Sprint 25)** | not invoked this sprint | OK |
| 8 | Return `PulseEvidence` dict | n/a (agent-side packaging) | — | OK |

**Note on `similarity_score` vs `similarity`:** the wrapper rename happens at `agent/tools/social_tools.py` so downstream agents/nodes see one canonical key (`similarity`). Documented in T19.1 wrapper docstring.

**T19.3 footnote — drifts surfaced during Pulse Analyst implementation (2026-04-30):**

| Drift | Spec field | Production reality | Resolution |
|---|---|---|---|
| **A** | `PulseEvidence.market_consensus[].key_arguments_pro` / `key_arguments_con` (list[str]) | `processing/gold_job.py:376-388` does not write either field to `platform_logic`; the consensus prompt (`prompts/consensus_summary.py:23-24`) emits only a single undifferentiated `key_findings` list. | T19.3 returns `[]` for both fields so the spec contract holds. Closing the gap requires consensus-prompt + gold_job changes — tracked as **KG-PHASE8-11** in `task_plan.md`. |
| **B** | `PulseEvidence.community_discussion[].community_sentiment: str` (e.g., "bullish") | `processing/gold_job.py:1744` stores `float(ai_meta.get("sentiment_score", 0.0))` in `[-1.0, 1.0]`. Zero downstream consumers (frontend, BFF, handoff §5.2) reference the string form. | Spec §8.4.2 corrected to `float, # -1.0 to 1.0` in T19.3. See "Correction (T19.3, 2026-04-30)" note in `agentic_hub_spec.md` §8.4.2. T19.3 returns the raw float. |

### 3.3 Market Bridge (§8.4.3)

| Step | Spec call | Existing function | Drift | Status |
|---|---|---|---|---|
| 1a | `momentum_vault.fetch_latest("polymarket", market_slug)` | `momentum_vault.fetch_latest(source_name, external_reference_id)` ([momentum_vault.py:162-206](../persistence/momentum_vault.py#L162-L206)) | None. | OK |
| 1b | `momentum_vault.fetch_time_series("polymarket", market_slug, hours=720)` | `momentum_vault.fetch_time_series(source_name, external_reference_id, hours=24)` ([momentum_vault.py:213-260](../persistence/momentum_vault.py#L213-L260)) | Default `hours=24` — wrapper passes 720 explicitly. | OK |
| 2 | `mapping_dict.lookup_by_canonical(canonical_event_id)` then per-link `fetch_latest` | `mapping_dict.lookup_by_canonical(canonical_event_id, platform=None, limit=100)` ([mapping_dict.py:159-218](../persistence/mapping_dict.py#L159-L218)); `momentum_vault.fetch_latest(...)` per link | None. **Expect frequently empty `linked_sources`** per D4 (sparse `canonical_event_id` pre-Phase 7). Tests fixture both populated and empty cases. | OK |
| 3 | `momentum_vault.fetch_fred_anomalies(days=14)` | `momentum_vault.fetch_fred_anomalies(days=7)` ([momentum_vault.py:299-339](../persistence/momentum_vault.py#L299-L339)) | Default `days=7` — wrapper passes 14 explicitly per spec. | OK |
| 4 | `momentum_vault.fetch_latest("googletrends", keyword)` per entity | same `fetch_latest(source_name, external_reference_id)` | None. | OK |
| 5 | `momentum_vault.fetch_latest("openweather"/"opensky", id)` when domain-relevant | same `fetch_latest(source_name, external_reference_id)` | None. | OK |
| 6 | Emit `vault_query_result` agentEvents | **deferred — D2 (Sprint 25)** | not invoked this sprint | OK |
| 7 | Return `MarketEvidence` dict | n/a (agent-side packaging) | — | OK |

`Public_Hype_Alert` from spec §8.4.3 step 4 is not on the row directly — it sits in `metadata_extension` JSONB on Google Trends Gold records. Wrapper extracts `metadata_extension->>'public_hype_alert'` (or equivalent key Phase 4F set; verify in T19.4 against Trends fixture). No new persistence function required.

---

## 4. Drift list (consolidated)

| # | Drift | Affected agent(s) | Resolution |
|---|---|---|---|
| D-1 | `knowledge_vectors.similarity_search` returns key `similarity`; `social_vectors.similarity_search` returns key `similarity_score`. | Researcher, Pulse Analyst | Wrappers normalize to `similarity` in `agent/tools/{knowledge,social}_tools.py`. Document in wrapper docstrings. |
| D-2 | Default values diverge from spec call values: `knowledge_vectors.similarity_search.limit=10` (spec 15); `momentum_vault.fetch_time_series.hours=24` (spec 720); `momentum_vault.fetch_fred_anomalies.days=7` (spec 14). | All three | Wrappers pass spec values explicitly; do not rely on defaults. |
| D-3 | `social_vectors.similarity_search` parameter shape diverges from `knowledge_vectors`: `min_impact` (int, sentinel-1) vs `min_impact_level` (Optional[int]); `platforms` (list) vs `source_platform` (str). | Pulse Analyst | Wrappers expose the spec-shape (`min_impact_level: Optional[int]`, `source_platforms: Optional[list[str]]`) and translate at the persistence boundary. |
| D-4 | `knowledge_vault.fetch_by_canonical_event` uses param `source_name`; `knowledge_vectors.fetch_by_canonical_event` uses `source_platform`. | Researcher (drill-down only) | Wrappers expose a single canonical name (`source_platform`) externally and translate. Cosmetic. |
| D-5 | `mapping_dict.find_similar_and_link` is Phase 7-deferred (Gold-job integration). | Market Bridge | Out of scope. Sprint 19 uses `lookup_by_canonical` only. Empty `linked_sources` is the expected default per D4. |
| D-6 | `momentum_vault.fetch_latest` row uses `metadata_extension` JSONB for source-specific fields (`anomaly_flags`, `public_hype_alert`, etc.). | Market Bridge | Wrapper extracts JSONB keys per source. No new persistence function needed. |

---

## 5. Blockers

**None.** Every call required by §8.4.1, §8.4.2, §8.4.3 already exists in `persistence/`. The drift items above are all resolvable inside the T19.1 wrapper layer with no changes to `persistence/*.py`.

**Service-isolation invariant preserved (CLAUDE.md §3.3):** `agent/tools/` is the only place where the hub touches `persistence/`. Agents (T19.2-19.4) and nodes (T19.5-19.8) call tools, never `persistence/` directly.

---

## 6. Wrapper API plan (informational — implemented in T19.1)

For each tool wrapper file the public surface is:

```
agent/tools/knowledge_tools.py
    similarity_search(query_embedding, limit, min_impact_level=None,
                      min_reliability=None, source_platforms=None) -> list[dict]
    fetch_full_text(doc_id) -> dict | None    # wraps knowledge_vault.fetch_by_doc_id

agent/tools/social_tools.py
    similarity_search(query_embedding, limit, min_impact_level=None,
                      min_reliability=None, source_platforms=None) -> list[dict]
                      # normalizes return key similarity_score → similarity
    fetch_raw_comments(social_id) -> dict | None   # wraps social_vault.fetch_by_social_id

agent/tools/market_tools.py
    fetch_latest(source_name, external_reference_id) -> dict | None
    fetch_time_series(source_name, external_reference_id, hours) -> list[dict]
    fetch_fred_anomalies(days) -> list[dict]

agent/tools/mapping_tools.py
    lookup_by_canonical(canonical_event_id, platform=None, limit=100) -> list[dict]
```

Smoke-check (D6 / Q2): one `pytest -m smoke` test per wrapper that exercises a tiny live query against dev Postgres and asserts a row shape. Skipped automatically when `DATABASE_URL` is unset, so unit-only CI stays green.

---

## 7. D10 wrap-up — state-field choice for Sprint 21

**Decision: add a new field `polymarket_candidates_considered: Optional[list[dict]]` to `ForecastState`** (do **not** reuse `clarification_candidates`).

### Rationale

| Aspect | Reuse `clarification_candidates` | Add `polymarket_candidates_considered` (chosen) |
|---|---|---|
| Semantic clarity | Mixed: "shown to user" + "auto-picked from" | One concern per field |
| Sprint 21 readability | `if state.clarification_candidates and state.awaiting_clarification:` — two flags to disambiguate | `if state.clarification_candidates:` — clarification UI is owed; `polymarket_candidates_considered` is audit-only |
| Lifecycle | `clarification_candidates` is consumed/cleared by the `chosenCandidateId` resume path; conflating it with audit data risks accidental overwrite | Audit field stays put for the whole session |
| Migration cost | Zero (state is `total=False`) | Zero (state is `total=False`) |
| Forward-compatibility | Couples Sprint 19 audit to Sprint 21 user-facing flow | Decoupled |

### What `polymarket_candidates_considered` carries (Sprint 19 contract)

```python
# Set by agent/nodes/query_understand.py when tier == "polymarket_backed"
state["polymarket_candidates_considered"] = [
    {
        "market_slug": str,
        "question": str,
        "match_confidence": float,   # 0.0-1.0
        "rank": int,                 # 1, 2, 3
    },
    # … up to 3 entries, ordered by match_confidence DESC
]
```

The auto-picked top-1 is also written to `state["polymarket_market"]` per spec §8.3.1; `polymarket_candidates_considered` is a parallel audit list that Sprint 21 reads to decide whether to surface candidates to the user (when match-confidence delta is too tight) without re-running the matching step.

### Sprint 21 hand-off note

Sprint 21's clarification logic (T21.1-T21.5) reads `polymarket_candidates_considered` to populate `clarification_candidates` when the top-2 confidence delta is below the threshold. The two fields stay separate; the auto-pick audit stays in `polymarket_candidates_considered` for the full session lifetime, while `clarification_candidates` only exists for sessions that paused for user input.

---

## 8. Approval gate

This audit is required to be approved by the user before T19.1 starts. On approval:

1. T19.1 begins with the wrapper API in §6.
2. T19.1 ships with Gate 1 unit tests **and** a `pytest -m smoke` smoke-check against dev Postgres before T19.2-T19.4 begin (D6 Gate B sequencing).
3. `ForecastState` gains the `polymarket_candidates_considered: Optional[list[dict]]` field as part of T19.5 (no separate state-schema task — the field is added when the producing node lands).

If anything in §3-§7 needs revision, the wrapper API in §6 changes accordingly before T19.1 begins.
