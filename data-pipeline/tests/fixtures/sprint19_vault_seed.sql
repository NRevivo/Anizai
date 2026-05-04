-- =============================================================================
-- Sprint 19 T19.1 — Gate B smoke-check vault seed (PERSISTENT)
-- =============================================================================
--
-- WHAT THIS IS
--   7 deterministic rows across the six vault tables that the agentic-hub
--   tool wrappers (agent/tools/*) read from. Lets `pytest -m smoke` verify
--   wrapper return shapes against a real Postgres without depending on the
--   Sprint 14-17 producer data (which is realistic but unstable for assertions).
--
-- LIFETIME — IMPORTANT FOR FUTURE INVENTORY RUNS
--   These rows are PERSISTENT BY DESIGN. They are NOT removed by the
--   tests/conftest.py cleanup fixtures (those scope to `test_<run_id>%`,
--   not `sprint19_smoke_%`). The smoke test (test_tools_smoke.py) does not
--   request any cleanup fixture either — it is read-only.
--
--   The 7 rows seeded here will live in the DB indefinitely. Future sprints
--   doing vault inventory must filter them out, e.g.:
--       SELECT COUNT(*) FROM knowledge_vault
--       WHERE canonical_event_id NOT LIKE 'sprint19_smoke_%';
--
--   To remove the seed entirely:
--       DELETE FROM knowledge_vectors WHERE canonical_event_id LIKE 'sprint19_smoke_%';
--       DELETE FROM knowledge_vault   WHERE canonical_event_id LIKE 'sprint19_smoke_%';
--       DELETE FROM social_vectors    WHERE canonical_event_id LIKE 'sprint19_smoke_%';
--       DELETE FROM social_vault      WHERE canonical_event_id LIKE 'sprint19_smoke_%';
--       DELETE FROM momentum_vault    WHERE canonical_event_id LIKE 'sprint19_smoke_%';
--       DELETE FROM mapping_dict      WHERE canonical_event_id LIKE 'sprint19_smoke_%';
--
-- IDEMPOTENCY
--   Knowledge_vault, knowledge_vectors, social_vault, social_vectors,
--   and mapping_dict rows use literal PK UUIDs and `ON CONFLICT DO NOTHING`
--   for re-run safety.
--
--   The two momentum_vault rows (polymarket + fred) use DELETE-before-INSERT
--   instead of ON CONFLICT, because their timestamp_utc is NOW()-relative
--   (refreshed on every re-seed run to keep within the 720h / 14d query
--   windows the smoke tests use — see KG-PHASE8-5 closure 2026-05-04).
--   The PK is composite (metric_id, timestamp_utc), so a NOW() insert
--   never matches the prior run's PK; without the DELETE, re-runs would
--   accumulate duplicate rows. The DELETE keys on the literal metric_id
--   UUID, so each re-seed produces exactly one fresh row per smoke target.
--
-- EMBEDDINGS
--   Every pgvector embedding is the deterministic unit vector e_0
--   (= [1.0, 0.0, 0.0, ...] of length 1536). Queries using the same vector
--   yield cosine similarity 1.0, making HNSW results predictable for
--   shape assertions.
--
-- ROW MAP (7 rows total)
--   1 knowledge_vault           canonical_event_id = sprint19_smoke_kv
--   1 knowledge_vectors         canonical_event_id = sprint19_smoke_kv
--   1 social_vault              canonical_event_id = sprint19_smoke_sv
--   1 social_vectors            canonical_event_id = sprint19_smoke_sv
--   1 momentum_vault (polymarket)  canonical_event_id = sprint19_smoke_mv_pm
--   1 momentum_vault (fred+anomaly) canonical_event_id = sprint19_smoke_mv_fred
--   1 mapping_dict              canonical_event_id = sprint19_smoke_map
--
-- Spec references:
--   - data-pipeline/docs/sprint19_persistence_audit.md §6 (wrapper API plan)
--   - data-pipeline/docs/sprint19_persistence_audit.md §3 (per-tool drift)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- knowledge_vault — drill-down target for knowledge_tools.fetch_full_text
-- ---------------------------------------------------------------------------
INSERT INTO knowledge_vault (
    doc_id,
    document_hash,
    canonical_event_id,
    silver_data_ref,
    raw_data_ref,
    source_name,
    author,
    original_url,
    publish_date,
    full_text_raw,
    inverted_pyramid_lead,
    detected_entities,
    relevance_score,
    sniper_keywords,
    scrape_attempted
) VALUES (
    '00000000-0000-0000-0000-000000000a01'::uuid,
    'sprint19smokekvhash0000000000000000000000000000000000000000aaaa',
    'sprint19_smoke_kv',
    '00000000-0000-0000-0000-000000000a02'::uuid,
    '00000000-0000-0000-0000-000000000a03'::uuid,
    'newsapi',
    'sprint19-smoke-author',
    'https://example.test/sprint19-smoke-article',
    '2026-04-01 00:00:00+00',
    'Smoke-test article body for Sprint 19 wrapper Gate B.',
    'Smoke-test lead.',
    '[]'::jsonb,
    0.5,
    '[]'::jsonb,
    true
)
ON CONFLICT (doc_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- knowledge_vectors — query target for knowledge_tools.similarity_search
-- ---------------------------------------------------------------------------
INSERT INTO knowledge_vectors (
    signal_id,
    canonical_event_id,
    silver_data_ref,
    raw_data_ref,
    source_platform,
    entry_type,
    published_at,
    content_vitals,
    enrichment_ai,
    domain_context,
    embedding
) VALUES (
    '00000000-0000-0000-0000-000000000b01'::uuid,
    'sprint19_smoke_kv',
    '00000000-0000-0000-0000-000000000a02'::uuid,
    '00000000-0000-0000-0000-000000000a03'::uuid,
    'newsapi',
    'article',
    '2026-04-01 00:00:00+00',
    '{"title": "sprint19 smoke knowledge vector"}'::jsonb,
    '{"impact_level": 3, "reliability": 0.8}'::jsonb,
    NULL,
    (ARRAY[1.0::float8] || array_fill(0.0::float8, ARRAY[1535]))::vector(1536)
)
ON CONFLICT (signal_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- social_vault — drill-down target for social_tools.fetch_raw_comments
-- ---------------------------------------------------------------------------
INSERT INTO social_vault (
    social_id,
    canonical_event_id,
    raw_data_ref,
    source_name,
    platform_data
) VALUES (
    '00000000-0000-0000-0000-000000000c01'::uuid,
    'sprint19_smoke_sv',
    '00000000-0000-0000-0000-000000000c02'::uuid,
    'polymarket',
    '{"raw_comments": [{"id": "smoke_c1", "body": "sprint19 smoke comment"}]}'::jsonb
)
ON CONFLICT (social_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- social_vectors — query target for social_tools.similarity_search
-- ---------------------------------------------------------------------------
INSERT INTO social_vectors (
    signal_id,
    canonical_event_id,
    silver_data_ref,
    raw_data_ref,
    source_platform,
    entry_type,
    published_at,
    content_vitals,
    enrichment_ai,
    social_context,
    platform_logic,
    embedding
) VALUES (
    '00000000-0000-0000-0000-000000000d01'::uuid,
    'sprint19_smoke_sv',
    NULL,
    '00000000-0000-0000-0000-000000000c02'::uuid,
    'polymarket',
    'comment',
    '2026-04-01 00:00:00+00',
    '{"text": "sprint19 smoke social vector"}'::jsonb,
    '{"impact_level": 2, "reliability": 0.6}'::jsonb,
    '{"author_reliability": 0.6}'::jsonb,
    '{}'::jsonb,
    (ARRAY[1.0::float8] || array_fill(0.0::float8, ARRAY[1535]))::vector(1536)
)
ON CONFLICT (signal_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- momentum_vault — query target for market_tools.fetch_latest /
-- market_tools.fetch_time_series  (polymarket row)
-- ---------------------------------------------------------------------------
-- DELETE-before-INSERT for idempotency with relative timestamps.
--
-- Why: the polymarket row's timestamp_utc must stay within the 720h
-- (30-day) window queried by `market_tools.fetch_time_series`. The FRED
-- row must stay within the 14-day window queried by
-- `market_tools.fetch_fred_anomalies`. Hardcoded dates (originally
-- '2026-04-01' and '2026-04-29') age out of these windows over time —
-- KG-PHASE8-5 closure surfaced this when the polymarket smoke
-- test_market_fetch_time_series_returns_list_with_explicit_hours
-- failed at 33 days post-seed (Sprint 20 closeout, 2026-05-04).
--
-- Fix: use NOW()-relative timestamps so the rows are always fresh.
-- The PK is composite (metric_id, timestamp_utc), so a NOW() value
-- never matches the prior INSERT's timestamp — ON CONFLICT DO NOTHING
-- doesn't help here. Instead, DELETE by the literal smoke metric_id
-- before each INSERT, then re-insert with a fresh timestamp. This is
-- idempotent across re-seed runs and never accumulates duplicate rows.
-- ---------------------------------------------------------------------------
DELETE FROM momentum_vault
    WHERE metric_id = '00000000-0000-0000-0000-000000000e01'::uuid;

INSERT INTO momentum_vault (
    metric_id,
    canonical_event_id,
    source_name,
    external_reference_id,
    current_value,
    unit,
    status,
    timestamp_utc,
    change_24h,
    change_7d,
    change_30d,
    is_new_market,
    metadata_extension
) VALUES (
    '00000000-0000-0000-0000-000000000e01'::uuid,
    'sprint19_smoke_mv_pm',
    'polymarket',
    'sprint19_smoke_pm_slug',
    0.42,
    'probability',
    'active',
    NOW() - INTERVAL '12 hours',
    0.01,
    0.05,
    0.10,
    false,
    '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- momentum_vault — query target for market_tools.fetch_fred_anomalies
-- (fred row carrying anomaly_flags so the wrapper has at least one row to
--  return; smoke test asserts shape only, not membership). Same DELETE-
-- before-INSERT pattern as the polymarket row above for the same reason.
-- ---------------------------------------------------------------------------
DELETE FROM momentum_vault
    WHERE metric_id = '00000000-0000-0000-0000-000000000e02'::uuid;

INSERT INTO momentum_vault (
    metric_id,
    canonical_event_id,
    source_name,
    external_reference_id,
    current_value,
    unit,
    status,
    timestamp_utc,
    change_24h,
    change_7d,
    change_30d,
    is_new_market,
    metadata_extension
) VALUES (
    '00000000-0000-0000-0000-000000000e02'::uuid,
    'sprint19_smoke_mv_fred',
    'fred',
    'sprint19_smoke_fred_series',
    3.14,
    'index',
    'active',
    NOW() - INTERVAL '12 hours',
    NULL,
    NULL,
    NULL,
    false,
    '{"anomaly_flags": {"is_anomaly": true, "z_score": 4.2, "direction": "up"}}'::jsonb
);

-- ---------------------------------------------------------------------------
-- mapping_dict — query target for mapping_tools.lookup_by_canonical
-- ---------------------------------------------------------------------------
INSERT INTO mapping_dict (
    mapping_id,
    canonical_event_id,
    platform,
    platform_specific_id,
    similarity_score,
    notes
) VALUES (
    '00000000-0000-0000-0000-000000000f01'::uuid,
    'sprint19_smoke_map',
    'polymarket',
    'sprint19_smoke_map_slug',
    0.95,
    'sprint19 smoke seed'
)
ON CONFLICT (platform, platform_specific_id) DO NOTHING;
