# polymarket_price_and_coverage.md
> Domain: A — Pipeline
> Type: Sprint Plan
> Last updated: 2026-07-28
> TL;DR: The Polymarket producer stores a probability of `0.0` on every row it has ever
> written (0 of 93,607), and only ever sees Gamma's oldest 100 markets. This plan fixes
> both — real prices read from the Gamma object itself, and coverage controlled by
> ordering + tag filtering + pagination — plus the three fabricated/misleading fields
> that become visible once prices are real. Every API fact below was verified against
> the live Gamma/CLOB API on 2026-07-28; every code anchor against `main` the same day.
> **Sequencing constraint: T5 must not ship before the Domain-B resolver index (§1 D7),
> or forecasts start failing outright instead of merely rendering empty.**

## Navigation
- §0 — Why this exists — the empty Market Benchmark card and the 0-of-22 baseline
- §1 — Closed decisions & verified facts — the frame; do not re-litigate
- §2 — Contracts — the REST payload before and after
- §3 — Task table — T1–T9 in implementation order
- §4 — Gates & test directives — including the REST-path test that does not exist today
- §5 — Execution notes — verification, sequencing, what this plan does not touch
- §6 — Skills

---

## §0 — Why this exists

The frontend's Market Benchmark card renders empty on every forecast, and when it
does render it shows a fabricated `0.0%` that the agent then reasons over in prose
("Polymarket odds at 0.0, indicating no market confidence"). Two independent
producer defects cause this.

**Defect 1 — the price is never extracted.** `_fetch_market_prices` builds a REST
payload with no `price` key (`ingestion/polymarket_producer.py:203-215`);
`map_price_update_to_silver` reads `raw.get("price", 0.0)`
(`processing/silver_job.py:170`) — a key that exists only on the WebSocket payload.
Every REST row therefore stores `current_value = 0.0`.

**Defect 2 — the producer sees the wrong 100 markets.** It requests `limit=500`
(`polymarket_producer.py:137`); Gamma caps at 100 and the producer never paginates.
Worse, the default ordering is **oldest-first**, so that page is entirely
`startDate=2025-05-02` novelty contracts ("Will Jesus Christ return before GTA VI?").

**Measured baseline (production, 2026-07-27/28):**

| Measure | Value |
|---|---|
| Polymarket rows in `momentum_vault` | 93,607 |
| …with a non-zero `current_value` | **0** |
| Distinct questions ever stored | 128 |
| Agent-produced forecasts, all time | 22 |
| …that resolved `tier_1` | 3 (13.6%) |
| …that carried a non-zero market price | **0** |

`current_value = 0` is Polymarket-specific: `fred` (2,190 rows) and `openweather`
(6,209 rows) store real values.

---

## §1 — Closed decisions & verified facts

These were established against the live API on 2026-07-28 (full evidence:
`dev/producer-fix-handover.md`). They are the frame, not open questions.

| # | Decision / fact | Evidence |
|---|---|---|
| **D1** | **Gamma already carries prices.** `outcomePrices` present on 100/100 live markets, alongside `lastTradePrice`/`bestBid`/`bestAsk`. The CLOB per-market call is **not needed for price**. | live sample, n=100 |
| **D2** | `outcomes` / `outcomePrices` / `clobTokenIds` arrive as **JSON-encoded strings**, not arrays. All require `json.loads`. | `'["Yes", "No"]'`, `'["0.505", "0.495"]'` |
| **D3** | The YES price is selected **by label** (`outcomes.index("Yes")`), never by index 0. Reading the wrong index silently reports the complement — a 3% market as 97%. | mirrors `server/src/repositories/trending.repository.ts:206-214` |
| **D4** | **`tokens` never exists on Gamma** (0/100). It is the CLOB shape. This is why `_token_ids` is permanently empty and the WebSocket has never subscribed. | live sample |
| **D5** | **Default ordering is controllable**: `&order=volume24hr&ascending=false` works and puts the high-volume Fed markets on page 1. One parameter, independent of pagination. | live |
| **D6** | **Tag filtering works at fetch time**: `/events?tag_id=2` → 20/20 results genuinely carry that tag. Tags live on the **event**; markets carry none. | live |
| **D7** | **Resolver timeout collision — hard sequencing constraint.** `find_polymarket_market_by_question` runs an unindexed seq scan: measured **415 ms at 93,607 rows** (`EXPLAIN ANALYZE`, no trigram index). At ~2,100 markets × 288 sweeps/day the table reaches ~3M rows in days; linear extrapolation ⇒ **~13 s** against the **15 s** `PER_AGENT_TIMEOUT_S` (`agent/nodes/vault_query.py:69`). On breach `_await` raises `AgentProcessingError` (`:149-152`) and the **whole forecast fails** rather than degrading. **Tag filtering (D6) is the load control that keeps this safe, not an optional nicety.** | measured 2026-07-28 |
| **D8** | Offset paging works to **offset 2000**; **2100 fails** with `offset too large, use /markets/keyset`. Deeper paging needs the keyset endpoint. | live |
| **D9** | `/events` paginates **and** nests markets (100 events / 1,194 markets per page), giving the event→market hierarchy at identical cost. `market["events"][0]` also carries the parent from the other direction. | live |
| **D10** | Unrecognised outcome labels **emit nothing** — no sentinel, no DLQ. WARNING + a `skipped` counter on the `Market discovery` INFO line. A sentinel would recreate exactly the bug being fixed. | brief, Part B3 |

**Out of scope, confirmed with the domain owner:** backfilling the 93,607 zero rows
(tables are being wiped before the next collection run), reviving the WebSocket,
`momentum` (D3-fabricated), `unit = "USD"`, and anything in `silver_job.py`.

---

## §2 — Contracts

### §2.1 REST payload — before → after

`_fetch_market_prices` return value (`polymarket_producer.py:203-215`):

```diff
  {
    "payload_type":    "price_update",
    "ingestion_mode":  "rest_snapshot",
    "market_id":       market.get("id", ""),
    "condition_id":    condition_id,
    "question":        market.get("question", ""),
-   "tokens":          tokens,                  # from a CLOB round-trip
+   "price":           <YES price, float>,      # THE FIX — key silver_job already reads
+   "outcome_prices":  {"Yes": 0.505, "No": 0.495},
+   "clob_token_ids":  ["980224…", "538315…"],  # parsed; replaces `tokens`
+   "parent_event_id": "23784",                 # C4 — catalog linkage
+   "end_date_iso":    "2026-07-31",            # C4 — already fetched, now preserved
+   "market_status":   "active" | "closed" | "archived",   # C4 — real, not hardcoded
    "volume_24h_usd":  volume_24h,
    "liquidity_usd":   float(market.get("liquidity", 0) or 0),
    "end_date":        market.get("endDate", ""),
-   "whale_alert":     whale_alert,             # 24h-volume proxy — see T4
+   "whale_alert":     False,                   # REST cannot detect a single trade
    "fetched_at":      ...,
  }
```

**`price` is the only key `silver_job` needs.** It already reads it
(`silver_job.py:170`) — the mapper is unchanged and Flink is **not** resubmitted.
Producer image build only.

### §2.2 Price scale (C6)

The producer emits **0–1 probability**, matching `outcomePrices` upstream and the
`current_value` the agent already reads as `current_odds`. Stated explicitly because
the agent writes prose about this number: if it were read as a percentage the text
would say "0.5% chance" for a coin-flip market while the frontend rendered correctly.

### §2.3 Row identity (C5)

One row per **market**, carrying the **YES** price, keyed on `condition_id`. The NO
price survives in `outcome_prices`; `clob_token_ids` is preserved for a future
WebSocket revival and the catalog. `asset_id` is not used on the REST path, so the
`external_reference_id` key-space ambiguity does not arise here.

---

## §3 — Task table

Ordered. Each task is one reviewable diff; none is committed without review.

| # | Task | File | Status |
|---|---|---|---|
| **T1** | `.gitignore`: add `data-pipeline/PIPELINE_REPORT.md` (`dev/` was already ignored — `.gitignore:27`) | `/.gitignore` | `[x]` **done** |
| **T2** | Extract the YES price from `outcomePrices` by label; add `price` + `outcome_prices` to the payload. Parse the JSON-encoded strings. Drop the CLOB round-trip for price. | `ingestion/polymarket_producer.py` | `[ ]` |
| **T3** | Unrecognised/missing outcome labels → **emit nothing**, WARNING with market id + observed labels, increment a `skipped` counter (D10). | `ingestion/polymarket_producer.py` | `[ ]` |
| **T4** | `whale_alert`: REST cannot detect a single-trade whale, so set `False` and log once at startup that REST whale detection is unavailable. (Today it fires on any market with ≥$100k **24h volume** — i.e. constantly, and `volume24hr` is present 100/100 so the branch is live.) | `ingestion/polymarket_producer.py` | `[ ]` |
| **T5** | Coverage: `&order=volume24hr&ascending=false` **(cheap, ships first)**, then `/events` + tag filtering (D6) + offset pagination to the D8 ceiling. **Blocked on D7 — see §5.** | `ingestion/polymarket_producer.py` | `[ ]` |
| **T6** | Safety bound must be **loud**: hitting the page/market cap logs a WARNING with actual numbers. A bound that truncates silently is the original bug with a bigger number. | `ingestion/polymarket_producer.py` | `[ ]` |
| **T7** | Preserve `parent_event_id`, `end_date_iso`, real `market_status` in the payload (C4). Producer-side only — persistence is Domain B's. | `ingestion/polymarket_producer.py` | `[ ]` |
| **T8** | Observability: `PYTHONUNBUFFERED=1`, and extend the `Market discovery` INFO line to `fetched / passed filter / skipped`. | `infrastructure/Dockerfile.polymarket`, producer | `[ ]` |
| **T9** | **REST-snapshot test + mock** — see §4. | `tests/mocks/`, `tests/test_ingestion/` | `[ ]` |

---

## §4 — Gates & test directives

**The suite currently tests the dead path.** There is exactly one Polymarket price
mock — `tests/mocks/polymarket_price_update.json` — and it is a **WebSocket**
`last_trade` payload carrying `"price": 0.67`. There is **no REST-snapshot mock at
all**. That is why 79 days of zeros passed CI green.

T9 must add:

1. `tests/mocks/polymarket_rest_snapshot.json` — a realistic REST payload built from
   a **real** live Gamma object (JSON-encoded `outcomes`/`outcomePrices`).
2. A test asserting `map_price_update_to_silver` yields **non-zero `current_value`**
   for it — the assertion that would have caught this on day one.
3. A test for the YES-by-label selection: a market whose outcome order is
   `["No", "Yes"]` must still yield the Yes price (guards D3).
4. A test for T3: an unrecognised label emits **no record**, not a `0.0`.

**Acceptance is not a payload inspection.** It is **a row in `momentum_vault` with a
non-zero `current_value`, through the full chain** — locally via `docker-compose`,
then confirmed in a cloud window. Mocks are legitimate downstream of the bug; they
must not be used to validate the price fix itself, since the bug lives in the seam
between payload and mapper.

---

## §5 — Execution notes

**Sequencing — the one hard constraint.** T2/T3/T4/T7/T8/T9 are safe to ship in any
order. **T5 (coverage) is gated on D7.** Raising the market universe ~21× without the
Domain-B trigram index (or the matcher move off the time-series table) walks the
resolver into the 15 s timeout within days, and the failure mode is a **failed
forecast**, not an empty card — strictly worse than today. Two acceptable unblocks:

1. Domain B lands the index / new matcher first, **or**
2. T5 ships **with tag filtering enabled from the start** (D6), which holds the row
   growth to roughly today's order of magnitude.

Do not ship untagged full-universe pagination against the current resolver.

**Recommended proof before full-volume collection.** A narrow vertical slice: ingest
one known event (the Fed decision field) with real prices and tag filtering on, then
run one picker-submitted forecast against it. That exercises price → vault → resolver
→ card on a handful of rows, with no timeout exposure.

**No deploy, no cluster mutation, no commits without review.** Producer changes are
isolated to `Dockerfile.polymarket`; `silver_job.py` is untouched and Flink is not
resubmitted.

**What this plan deliberately does not fix** (Domain B, recorded so it is not
rediscovered): the fabricated `momentum` block, `unit = "USD"` on a probability,
`bid_ask_spread`/`is_divergent`/`resolution_rules` hardcoded placeholders, the absent
value validation that would have caught a 0.0 probability on day one, the DLQ record
mislabelling (`source_topic: process.silver.social_pulse` on `Structured_Metric`
payloads), and the WebSocket revival.

---

## §6 — Skills

- `infrastructure` — Dockerfile / env changes (T8)
- `code-review` — the four-gate model and test protocol (§4)
- `bugfix` — before/after comparison discipline; this plan's "before" is §0's table
