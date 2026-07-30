# polymarket_completion.md

> Domain: A — Pipeline (with a marked Domain-B track and a marked partner track)
> Type: Sprint Plan
> Last updated: 2026-07-30
> TL;DR: The collaborator's fix (commits `8a47723`, `3da2f00`) made Polymarket prices
> real but left the coverage half untouched — the producer still collects Gamma's
> oldest 100 novelty markets. This plan completes the chain end to end: the right
> markets in, the catalog fields surviving the Silver layer, a deterministic
> question→market join, refusal instead of fabrication, and both frontend charts
> lit. Target: working in cloud within three days, before the database wipe and the
> multi-day collection run.
> **Sequencing constraint: nothing here ships to cloud until §5's local gate passes,
> and the Silver edit (Track S) is the only change in this plan that requires a Flink
> cancel + resubmit — it is done once, completely.**

## Navigation

- §0 — Where we are — what the collaborator delivered and what he did not
- §1 — Closed decisions & verified facts — the frame; do not re-litigate
- §2 — Contracts — the three seams this plan changes
- §3 — Task table — Tracks P / S / A / V
- §4 — Gates & test directives
- §5 — Execution, sequencing, and the cloud path
- §6 — Partner track (client/ + server/) — Ron relays; not ours to implement
- §7 — Carry-over open items from the pre-merge review
- §8 — Skills

---

## §0 — Where we are

**Merged and working (collaborator, 2026-07-30):** the YES price is extracted from
the Gamma object by label; markets that cannot be priced emit nothing rather than a
sentinel; `whale_alert` is `False` on the REST path; the catalog fields
(`parent_event_id`, `end_date_iso`, `market_status`, `clob_token_ids`,
`outcome_prices`) are present in the producer payload; a REST-snapshot mock and test
exist for the first time.

**Not delivered:** T5 (coverage) and T6 (loud bound) from
`polymarket_price_and_coverage.md`. `_fetch_active_markets` is unchanged —
`{"active": "true", "closed": "false", "limit": 500}`, no ordering, no pagination,
no topic filter, still `/markets` rather than `/events`. Gamma silently caps that at
100 and defaults to oldest-first, so the collected universe is novelty contracts
("Will Jesus Christ return before GTA VI?"). **Post-fix the system stores correct
prices for the wrong markets** — and a wipe now would bake exactly that into the
clean corpus.

**Three seams downstream of the producer that this plan closes:**

1. **The Silver mapper drops everything the collaborator added.** `metadata_extension`
   is a fixed 7-key dict; `clob_token_ids`, `parent_event_id`, `end_date_iso`,
   `market_status` and `outcome_prices` all fall on the floor
   (`processing/silver_job.py`, `map_price_update_to_silver`). `data_point.status` is
   hardcoded `"active"` and `unit` is hardcoded `"USD"` for a probability.
2. **The vault stores `metadata_extension` verbatim as JSONB**
   (`persistence/momentum_vault.insert`), so carrying the fields costs **no schema
   change** — only the mapper edit. Note `core_identity.parent_id` (the Polymarket
   `market_id`) is *not* among the INSERT columns and does not survive either.
3. **The frontend is further along than assumed.** The partner has shipped a
   market-first picker: `server/src/repositories/trending.repository.ts` exposes
   `TrendingMarket { conditionId, question, groupItemTitle, probability, volume24h }`
   and the picker submits the market's `question` **verbatim** plus its `conditionId`.
   Its own comment records that `conditionId` is the deterministic join key to
   `momentum_vault.external_reference_id`. **On the picker path there is no matching
   problem — there is an identifier.**

**What that reframes.** The 2026-07-25/26 run's single `1.0000` trigram match was not
evidence that fuzzy matching works; it is evidence that the picker pastes the market
question verbatim. Free-text matching remains unsolved and is **explicitly out of
scope here** (§5).

---

## §1 — Closed decisions & verified facts

Established against the live Gamma/CLOB API on 2026-07-28 and 2026-07-30, and against
`main` at `3da2f00`. These are the frame, not open questions.

### API facts

| # | Fact | Evidence |
|---|---|---|
| **F1** | `volume` is **cumulative lifetime** USD; `volume24hr` is a rolling 24h window. Event-level volume is the **exact arithmetic sum** of its nested markets (ratio 1.000 across all 2,475 target events; 0 deviations >1%). | live, n=2,475 |
| **F2** | `volume_min` on `/events` filters on **cumulative** `volume` — the correct field. Honoured, not ignored (`volume_min=1e6` → 100 results bounded at $1,014,901; `volume_min=5e8` → 3). | live |
| **F3** | `limit` is hard-capped at **100** on both `/events` and `/markets`, regardless of the value sent. `limit=500` is silently truncated — the original defect. | live |
| **F4** | Ordering is supported and honoured on both endpoints: `order=volume24hr&ascending=false`. Invalid order fields return **HTTP 422**, so a typo fails loudly. Verified order fields on `/events`: `volume24hr`, `volume`, `liquidity`, `startDate`. | live |
| **F5** | Offset paging works to **offset 2000**; ~2050+ returns 422 pointing at `/events/keyset`. The keyset cursor param is **`after_cursor`**, and it is honoured **only on the `/keyset` routes** — on plain `/events` it is silently ignored and returns page 1. | live |
| **F6** | **Unknown query params are silently accepted (HTTP 200).** A typo'd filter looks like it works. Every filter must be verified by inspecting the returned set, never by the absence of an error. | live |
| **F7** | `/events` has **no `active` param**; `active=true`/`false`/absent return byte-identical lists. On `/markets` it is present but also does not filter. `closed=false` is the parameter that matters. | live |
| **F8** | `outcomes` / `outcomePrices` / `clobTokenIds` arrive as **JSON-encoded strings**, not arrays. On **market** objects `volume` and `liquidity` are also strings — use `volumeNum` / `liquidityNum` (floats). On **event** objects `volume` is already a float. | live |
| **F9** | A **missing `outcomePrices` key means "never traded", not an error.** Every market lacking it has cumulative volume of exactly $0 (n=7,224, max $0, zero exceptions). Above **any** non-zero volume threshold, coverage is 100%. | live |
| **F10** | `/events` nests markets and carries tags; **tags live on the event, not the market**. `market["events"][0]` carries the parent from the other direction. | live |
| **F11** | **`tokens` never exists on Gamma** (0/100) — it is the CLOB shape. This is why `_token_ids` is permanently empty and the WebSocket has never subscribed. | live |
| **F12** | A CLOB **price-history** endpoint exists: `GET {CLOB_API_BASE}/prices-history?market=<token_id>&interval=max` — ~712 points over a market's life, ~10-minute granularity. It is the only viable source for a history chart; our own vault is bursts separated by multi-week holes. | live |

### Selection decisions

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Filter at the EVENT level, on cumulative volume ≥ $500,000.** | **Measured end-to-end 2026-07-30** — the funnel P1 actually implements: 386 events clear $500k with `closed=false` → **253** carry a TARGET tag → 240 survive the hard-exclude pass → **192** survive the past-`endDate` drop → 2,712 nested markets → 2,421 non-closed → **1,396 collectable** (non-closed and priceable). ~33.5k rows/day at D7's hourly cadence, ~235k over a week-long run; the resolver measured 415 ms at 93,607 rows, so this sits far inside the 15 s `PER_AGENT_TIMEOUT_S`. The $500k→$1M band contains substantive questions (Trump impeachment, India–Pakistan strike, Iran enrichment, BoJ decision, OpenAI valuation) while $1M's floor is dominated by repetitive per-settlement Ukraine-map markets. **The money threshold does most of the work:** only 386 events in all of Polymarket clear $500k and 253 of them (66%) are already on-topic — so if V6 shows coverage is too thin, lowering the threshold is a one-parameter lever (~×1.4 at $250k). The earlier 252 / 3,187 / 1,490 figures were a pre-`endDate` estimate; one event in five that Polymarket reports as open has in fact already ended (D5). | measured |
| **D2** | **Market-level volume filtering is rejected.** | Measured: 17 of the top 25 markets ≥$1M are individual candidate legs of two 2028 nomination events (LeBron James, Oprah Winfrey, Kim Kardashian, MrBeast) — cheap perpetual longshots in 128-outcome fields, near-worthless as forecasting signal. It also severs each market from its parent event's context. The event is the coherent unit. |
| **D3** | **Topic set: 6 categories + geo regions. The tag list is §1.1 — that is the artifact P1 filters on.** Geopolitics/wars (incl. region tags), Politics, Elections, Macro & economy, Business, Technology. **Health and Science are dropped** — Polymarket has effectively no coverage (23 and 91 events respectively; no general health tag exists). **Crypto is excluded.** | Health/Science coverage arrives incidentally through Politics/Geopolitics. Crypto carries more events than all target categories combined but is overwhelmingly 1-market recurring price ticks; only 41 events carry both a target tag and a crypto tag, so excluding it costs almost nothing. |
| **D4** | **Do not subscribe by conflict noun.** Tags `war`, `conflict`, `invasion`, `military invasion`, `peace deals`, `hostage crisis`, `Sanctions`, `West Bank` all carry **zero** active events. Subscribe by region. | live |
| **D5** | **Filter by `endDate` in addition to `closed`.** 30 of 172 events at the $1M threshold have an `endDate` already in the past yet are still `closed=false`. Relying on `closed` alone means forecasting questions that have already ended. | live |
| **D6** | **Re-derive membership every run; never pin an event list.** Cumulative volume cannot decrease, so entry is one-way (~+3/day); exit happens only on resolution (~-11/day) — ~8%/day churn, ~90%+ day-over-day overlap. | live |
| **D7** | **Collection cadence: hourly, not 5-minutely.** The history chart comes from CLOB (F12), not from our time-series, so dense sampling buys nothing. At 1,490 live markets hourly this is ~36k rows/day (~250k over a week-long run) against the 93,607 rows that measured 415 ms on the resolver — well inside the 15 s `PER_AGENT_TIMEOUT_S`. | derived from F12 + measured resolver cost |
| **D8** | **The `LOG_INFO_SAMPLE_RATE=1.0` remedy is rejected.** The sampling policy exists for ~100 msg/s streams (`_SampledInfoFilter` docstring); a once-per-sweep summary is 24/day at the new cadence. The fix is a per-record exemption, not a global override. KG-A-17 (OOM mechanism unresolved) stays untouched. | code read + KG-A-17 |
| **D9** | **Track S is the single Flink-touching change and is done once, completely.** The producer change is an image build only. Because the wipe follows this work, `unit`, `status` and the dropped catalog fields are fixed in the same edit rather than left to be baked into the clean corpus. | reversal-cost reasoning |
| **D10** | **Free-text question→market matching is OUT OF SCOPE.** The picker path is deterministic (`conditionId`). A typed question that resolves to nothing must produce an explicit refusal plus a prompt to pick from the market screen — an honest state, not a broken one. | scope |

---

## §1.1 — TARGET tag list (the D3 artifact)

Event counts are **active, non-closed events carrying the tag**, measured 2026-07-30
across the full 16,152-event sweep. They are for judgement, not for code.

**How this list is used.** An event is IN if it carries **at least one** INCLUDE tag
(§1.1a–g). It is then dropped if it carries any HARD-EXCLUDE tag (§1.1h). Tags live on
the event, never the market (F10).

**Reconciled against the live API 2026-07-30 — OQ4 CLOSED.** The list is final at **98
tag ids**: the original 79 candidates plus the 19 in §1.1g found during reconciliation.
Per **F6** an unrecognised tag value returns HTTP 200, so verification was by inspecting
the returned set, never by absence of an error.

> **Thirteen tags return zero events inside the ≥$500k set and are deliberately KEPT:**
> `1013` Earnings · `102451` Earnings Calls · `103899` House Elections · `518` India ·
> `351` North Korea · `872` Pakistan · `101761` Trade War · `793` defense · `1476` eu ·
> `102786` Nov 4 Elections · `473` gpt · `131` interest rates · `132` monetary policy.
>
> **These are not dead tags.** `Earnings` carries 140 active events and `House
> Elections` 446 in the full universe — they simply do not clear the money threshold
> today. "Zero" here means zero *within the threshold set*, not zero overall. Removing
> them buys nothing and loses coverage the moment one of them crosses $500k. **Read
> every count in this section the same way: they are relative to the ≥$500k set.**

### §1.1a — Geopolitics / wars

Umbrella: `100265` Geopolitics (383) · `101970` World (180) · `101794` Foreign Policy
(26) · `464` Military Actions (11) · `1289` Nuclear (10) · `101761` Trade War (5) ·
`193` military (3) · `793` defense (3)

Regions and conflicts: `78` Iran (109) · `95` Russia (94) · `180` Israel (80) · `154`
Middle East (74) · `102486` Ukraine Map (66) · `96` Ukraine (56) · `303` China (42) ·
`270` putin (36) · `104010` Iran Ceasefire (29) · `246` Venezuela (27) · `734` UK (23) ·
`61` Gaza (15) · `192` NATO (14) · `103027` Ukraine Peace Deal (14) · `452` zelensky
(11) · `297` Hezbollah (10) · `101270` Turkey (9) · `102305` US-Iran (9) · `582` Houthis
(7) · `867` Taiwan (7) · `102475` Russia Capture (7) · `1476` eu (7) · `114` Syria (6) ·
`518` India (6) · `102498` Trump-Zelenskyy (6) · `351` North Korea (5) · `102477`
Trump-Putin (3) · `102824` Trump x al-Sharaa (2) · `1383` Poland (2) · `872` Pakistan (2)

> **D4 restated:** the conflict *nouns* — `79` war, `502` conflict, `593` invasion,
> `1308` military invasion, `335` peace deals, `1542` hostage crisis, `102620`
> Sanctions, `101850` West Bank — all carry **zero** active events. They are listed
> here only so nobody re-adds them believing they were forgotten.
>
> **Reach vs precision:** adding all 30 region tags on top of the category set nets
> only **+7 events**, because region-tagged events almost always also carry
> Politics/Geopolitics/World. They earn their place through precision (they are what
> a future category filter would key on), not coverage.

### §1.1b — Politics

`2` Politics (1,559) · `126` Trump (277) · `514` Congress (42) · `100199` Senate (22) ·
`1628` Courts (22) · `102886` President (8)

> `1628` Courts is a judgement call worth surfacing: it admits legal questions such as
> "Harvey Weinstein prison time?" ($1.14M, tagged `Courts,Culture,Movies`). Defensible
> as political/legal forecasting, but if Ron wants it out, dropping this one tag is the lever.

### §1.1c — Elections

`144` Elections (970) · `1101` US Election (754) · `102289` Midterms (559) · `103899`
House Elections (446) · `102786` Nov 4 Elections (443) · `1597` Global Elections (212) ·
`264` Primaries (148) · `902` primary elections (119) · `104743` Main Election (110) ·
`105438` Intl Election Props (99)

### §1.1d — Macro & economy

`120` Finance (476) · `102676` Equities (245) · `100328` Economy (166) · `1013` Earnings
(140) · `102000` Macro Indicators (59) · `702` Inflation (28) · `101800` Economic Policy
(22) · `159` Fed (21) · `370` GDP (16) · `103339` Fed Chair (5) · `131` interest rates
(4) · `132` monetary policy (3)

> `102134` Hit Price (115) and `102368` Pre-Market (166) are **deliberately omitted**.
> The substantive ladder markets they cover ("What will S&P 500 hit in July 2026?")
> already arrive via `120` Finance; including them separately drags in high-frequency
> price-ladder noise.

### §1.1e — Business

`107` Business (93) · `600` IPOs (45) · `102599` IPO (36) · `102451` Earnings Calls (2)

> Business is weak **as a tag** — most business content sits under `120` Finance,
> `102676` Equities and `1013` Earnings, all already in §1.1d. Expect little marginal
> reach from this group.

### §1.1f — Technology

`1401` Tech (226) · `439` AI (167) · `101999` Big Tech (137) · `537` OpenAI (40) · `285`
sam altman (17) · `662` llm (7) · `473` gpt (6) · `102464` GPT-5 (3)

### §1.1g — Added after live reconciliation (2026-07-30)

Nineteen tags carrying events in the six categories that the original candidate list
missed. **Net new events today: zero** — every event carrying them is already caught by
a broader tag. They are kept as **tail insurance**: an event tagged only `Fed Rates` and
not `Fed`, or only `Strait of Hormuz` and not `Iran`, would otherwise be missed
entirely. Cost is zero.

`104039` U.S. x Iran (18) · `415` Peace Deal (15) · `101206` World Elections (14) ·
`104005` Iran Regime (11) · `101191` Trump Presidency (11) · `262` Strait of Hormuz (10) ·
`309` Oil (10) · `100196` Fed Rates (9) · `101550` Jerome Powell (9) · `104064` Israel x
Iran (9) · `165` United States (8) · `849` Lebanon (7) · `101031` Commodities (6) ·
`102304` Khamenei (5) · `105048` OpenAI IPO (5) · `216` zelenskyy (5) · `103996` Reza
Pahlavi (5) · `102868` Cuba (5) · `101569` Greenland (5)

> Reconciliation also surfaced `74` Science (11) and `103037` Climate & Science (8)
> inside the threshold set. **Not added** — D3 drops Science deliberately.

### §1.1h — HARD EXCLUDE (applied after the include pass)

An event carrying any of these is dropped **even if it also carries an INCLUDE tag**.

**Crypto (D3):** `21` Crypto (3,533) · `1312` Crypto Prices (3,292) · `336` token launch
(102) · `100171` Stablecoins (9) · `136` Airdrops (4) · `235` Bitcoin (506) · `39`
Ethereum (487) · `101267` XRP (476) · `818` Solana (475) · `101312` Ripple (474) ·
`100178` Dogecoin (448) · `102716` BNB (447)

**Platform internals:** `102169` Hide From New (3,680) — a Polymarket display flag, not
a topic.

**Sports / games** (only relevant if a target tag co-occurs): `1` Sports (9,743) ·
`100639` Games (9,298) · `100350` Soccer (7,169) · `64` Esports (835) · `65` league of
legends (179).

**Candidate exclusions — RESOLVED 2026-07-30, DO NOT EXCLUDE:** `101757` Recurring ·
`102127` Up or Down · `102892` 5M · `102467` 15M · `102175` 1H · `84` Weather. Measured:
excluding any of them drops **zero** events from the ≥$500k target set, at both the
post-hard-exclude (240) and survivor (192) stages. The cumulative threshold already
removes them. **They are omitted from `EXCLUDE_TAGS` entirely — no machinery.** The
concern that legitimate periodic macro events (Fed decisions, CPI releases) carry
`Recurring` was the reason for verifying first; it did not materialise, and neither did
the need for the exclusion.

> **Why a blocklist at all**, given the $500k cumulative threshold: an event that
> recurs frequently enough accumulates lifetime volume across many cycles and can
> cross the threshold on churn rather than on interest. The threshold filters money,
> not meaning.

---

## §2 — Contracts

### §2.1 Market selection — before → after

`_fetch_active_markets` (`ingestion/polymarket_producer.py`):

```diff
- url = f"{GAMMA_API_BASE}/markets"
- params = {"active": "true", "closed": "false", "limit": 500}
- # client-side filter: volume >= $10,000
+ url = f"{GAMMA_API_BASE}/events"
+ params = {
+     "closed":     "false",
+     "limit":      100,                    # F3 — the real cap; asking for more lies
+     "volume_min": 500_000,                # D1, F2 — cumulative, server-side
+     "order":      "volume24hr",           # F4
+     "ascending":  "false",
+     "offset":     <paged, F5>,
+ }
+ # then, client-side:
+ #   - keep events carrying >=1 TARGET tag (D3)
+ #   - drop events whose endDate is in the past (D5)
+ #   - flatten to nested markets, skipping closed / never-traded ones (F9)
```

The producer emits **one Bronze record per nested market**, as today. The parent event
identity travels on each record (`parent_event_id`, plus the event title — see §2.2).

### §2.2 The Silver seam — what must stop being dropped

`map_price_update_to_silver` (`processing/silver_job.py`). The mapper is
Polymarket-only; FRED and the other structured-metric sources have their own mappers,
so these changes do not leak.

```diff
  "data_point": {
      "current_value": float(raw.get("price", 0.0)),
-     "unit":          "USD",
+     "unit":          "probability",              # it is 0-1, never dollars
-     "status":        "active",
+     "status":        raw.get("market_status", "active"),
      "timestamp_utc": (
-         raw.get("timestamp") or envelope.get("producer_timestamp", "")
+         raw.get("timestamp")
+         or raw.get("fetched_at")                 # the key REST actually sends
+         or envelope.get("producer_timestamp", "")
      ),
  },
  "metadata_extension": {
      … existing 7 keys …
+     "clob_token_ids":  raw.get("clob_token_ids", {}),   # dict by label (P8) — chart join key
+     "market_id":       raw.get("market_id", ""),        # core_identity.parent_id dies at INSERT
+     "parent_event_id": raw.get("parent_event_id", ""),
+     "event_title":     raw.get("event_title", ""),      # the picker's headline
+     "end_date_iso":    raw.get("end_date_iso", ""),     # D5 — resolved-market guard
+     "outcome_prices":  raw.get("outcome_prices", {}),   # NO price survives (C5)
  },
```

`event_title` is new in the producer payload too (P1) — without it nothing downstream
can name the parent question.

**Blast radius:** `metadata_extension` is stored verbatim as JSONB
(`momentum_vault.insert`), so **no migration**. But this is Flink code: it requires
cancel + resubmit of **both** the Silver and Gold jobs (project rule — either job's
code change requires both).

### §2.3 The agent seam — resolution order

```
1. conditionId supplied by the picker
     → exact lookup on momentum_vault.external_reference_id      (deterministic)
2. no conditionId, but the question matches verbatim
     → existing pg_trgm resolver                                 (unchanged)
3. conditionId supplied but absent from the vault
     → one live Gamma lookup, forecast proceeds                  (safety net)
4. nothing resolves
     → explicit refusal + prompt to use the market screen        (D10)
```

Steps 1 and 3 are new. Step 3 is the net that makes us independent of whether the
partner aligns his picker filters (§6).

**Resolved-market guard (D5):** before forecasting, check `end_date_iso` from the
vault row; if it is in the past, or absent, confirm live. Stored `status` is stale by
construction — a market can resolve between the sweep and the question.

---

## §3 — Task table

Ordered within each track. Tracks P and A touch disjoint files and can run in
parallel across two Claude Code sessions; Track S must land before Track V.

### Track P — Producer (`ingestion/`, `infrastructure/`)

| # | Task | File | Status |
|---|---|---|---|
| **P1** | Switch market discovery to `/events` with `volume_min=500000`, `order=volume24hr&ascending=false`, `closed=false`, offset pagination to the F5 ceiling. Apply the TARGET tag filter (D3) and the `endDate` filter (D5) client-side. Flatten to nested markets. Add `event_title` to the payload. Use `volumeNum` for markets, `volume` for events (F8). | `polymarket_producer.py` | `[x]` |
| **P2** | Verify every filter by inspecting the returned set, not by absence of an error (F6). Log the funnel: events fetched → passed tag filter → passed endDate filter → markets flattened → emitted → skipped. | `polymarket_producer.py` | `[x]` |
| **P3** | Loud bound: hitting the page cap, the offset ceiling, or a shrinking result set logs a **WARNING with actual numbers**. A bound that truncates silently is the original bug with a bigger number. | `polymarket_producer.py` | `[x]` |
| **P4** | Change `PRICE_POLL_SEC` and `MARKET_REFRESH_SEC` to hourly (D7). Since `_fetch_market_prices` no longer makes HTTP calls, the price sweep is a pure transform over the cached list — the two loops can be collapsed if that simplifies; do not restructure further than needed. | `polymarket_producer.py` | `[x]` |
| **P5** | Add an `always_emit` exemption to the INFO sampling filter (`if getattr(record, "always_emit", False): return True`) and mark the sweep-summary and funnel lines with `extra={"always_emit": True}`. Default `False` — no behaviour change for any existing caller. **Isolated commit** (KG-C-3). | `utils/logging_config.py`, `polymarket_producer.py` | `[x]` |
| **P6** | Strike the closing recommendation from the `Dockerfile.polymarket` comment block, or scope it explicitly to the producer with a pointer to KG-A-17. Keep the 1%-sampling diagnosis — it is correct and valuable. | `infrastructure/Dockerfile.polymarket` | `[x]` |
| **P7** | `.gitignore`: the existing `/dev/` rule is root-anchored and does not match `data-pipeline/dev/`. Add it. | `/.gitignore` | `[x]` |
| **P8** | Emit `clob_token_ids` as a **dict keyed by outcome label** (`{"Yes": "…", "No": "…"}`), mirroring `outcome_prices`, instead of a positional list. **Reason: the CLOB history call (OQ1) needs the YES token specifically, and selecting it positionally reports the complement** — a 7% market charted as 93%. This is the D3 label-not-index trap recurring one level down, at the token. Track S carries the dict through unchanged; update G2 accordingly. | `polymarket_producer.py` | `[x]` |

### Track S — Silver (`processing/`) — the single Flink-touching change

| # | Task | File | Status |
|---|---|---|---|
| **S1** | Carry `clob_token_ids`, `parent_event_id`, `event_title`, `end_date_iso`, `outcome_prices` and **`market_id`** into `metadata_extension` (§2.2). **`market_id` added 2026-07-30, AFTER S1 was first marked done — re-open it:** the market id currently travels only as `core_identity.parent_id`, which is not among `momentum_vault.insert`'s 13 columns and is therefore dropped at the INSERT, so Polymarket's market id does not reach the vault at all today. One key, no schema change, same dict already edited. | `silver_job.py` | `[x]` |
| **S2** | `data_point.status` reads `raw["market_status"]` instead of the hardcoded `"active"`. | `silver_job.py` | `[x]` |
| **S3** | `data_point.unit` becomes `"probability"` for the Polymarket price path. Confirm no consumer branches on the literal `"USD"` before changing it. | `silver_job.py` | `[x]` |
| **S4** | `timestamp_utc` falls back to `raw["fetched_at"]` (the key REST actually sends) before the envelope timestamp. | `silver_job.py` | `[x]` |
| **S5** | Add a Polymarket-scoped probability range guard: a value outside **`[0.0, 1.0]` inclusive** routes to DLQ (`failed_stage="price_range_guard"`) rather than being stored. **`0.0` is a legitimate market state** — live data contains `["0", "1"]` markets — so the guard's job is scale errors (negative, >1), not zeros; the original bug is now structurally unreachable because an unpriceable market emits no record at all. Add separately a **sweep-level signal**: if 100% of emitted prices in a sweep are `0.0`, log a WARNING. Placement follows the existing precedent of the Google Trends `[0,100]` guard inline in the Silver branch, so source-scoping is structural rather than conditional — not a new rule in the shared validator that FRED and OpenWeather also call. | `silver_job.py` | `[x]` |
| **S6** | Fix the Polymarket DLQ source-topic mislabelling: `Structured_Metric` DLQ records carry `"source_topic": "process.silver.social_pulse"`, which is the comment path, not the price path. **This is now load-bearing rather than cosmetic** — S5 routes real records to the DLQ, and a lying `source_topic` sends triage to the wrong stream. Same file as S1–S5; no extra deployment cost. Reported by the collaborator, `dev/producer-fix-handover.md` §7. | `silver_job.py` | `[ ]` |

### Track A — Agent (`agent/`, `persistence/`) — Domain B

| # | Task | File | Status |
|---|---|---|---|
| **A1** | Exact lookup by `conditionId` → `momentum_vault.external_reference_id`, taking the latest row. Used whenever the frontend supplies one; the pg_trgm resolver becomes the fallback, unchanged. | `persistence/momentum_vault.py`, `agent/nodes/vault_query.py` | `[x]` |
| **A2** | Establish where the `conditionId` submitted by the picker actually arrives — Firestore `forecastQueries`, or the Express BFF payload. **Read the live path before writing code**; do not assume. Record the field name in this plan. | (investigate) | `[x]` |
| **A3** | Live-fetch safety net: `conditionId` present but absent from the vault → one Gamma lookup, forecast proceeds, and the gap is logged as WARNING. Must degrade (not fail) if Gamma is unreachable. | `agent/nodes/vault_query.py` | `[x]` |
| **A4** | Resolved-market guard: refuse (or explicitly mark as retrospective) when the market's end date has passed. | `agent/nodes/vault_query.py` | `[x]` |
| **A5** | No-match behaviour: explicit refusal with a prompt to use the market screen, rather than a silent `tier_2` carrying a fabricated benchmark. Feeds `marketComparisonInsight`. | `agent/nodes/synthesize.py` | `[x]` |
| **A6** | A resolved market must classify **`tier_1`**. Verify the classification actually flips — 0/7 did on the last cloud run. | `agent/` (locate) | `[ ]` |
| **A7** | **`predictionSeries` — verify, do not rebuild.** Sprint 22 T22.4 already shapes and persists Polymarket price history into the `predictionSeries` subcollection on the Tier-1 path; it has never fired because a market was never resolved. Read the write site, confirm it uses the CLOB history endpoint (F12) with the token ids now surviving Track S, and **record the exact document shape** into §6 so the partner builds against it. | `agent/nodes/write_to_firestore.py` | `[x]` |
| **A8** | Confirm `marketProbability` is written on the Tier-1 path. This single field is the only gate on the comparison chart. | `agent/nodes/write_to_firestore.py` | `[x]` |

### Track V — Verification & cloud

| # | Task | Status |
|---|---|---|
| **V0** | **Purge the Bronze backlog BEFORE Flink comes up.** At the collaborator's 2026-07-29 close, `ingest.bronze.polymarket` end offsets were `0:16624, 1:16699, 2:19996` — **~53k messages**, all zero-price rows against the old novelty-market universe, accumulated since the last vault write on 2026-07-27. Flink drains them **before** reaching anything this sprint produces. Use `kafka-delete-records` (established: skips a backlog in minutes rather than waiting for forward drain) rather than waiting it out. **Order matters: purge, then bring Flink up.** Doing it after means the old rows are already in the vault and V5/V6 cannot distinguish them from new ones. | `[ ]` |
| **V1** | Local `docker-compose` full chain. **Acceptance: a row in `momentum_vault` with a non-zero `current_value`, a populated `clob_token_ids`, and a real `status`** — not a payload inspection. | `[ ]` |
| **V2** | Producer image build + push (`Dockerfile.polymarket` only — isolated from the other producers). | `[ ]` |
| **V3** | Flink **cancel + resubmit of BOTH jobs** (Silver and Gold). Pod restart does not pick up new images or config. **A running jobmanager does not imply a submitted job** — confirm via `flink list` or the UI; this is the step most likely to be silently skipped. Flink has been at `0/0` in cloud since 2026-07-27, so this is a cold bring-up, not a restart. | `[ ]` |
| **V4** | Agent image build + rollout for Track A. | `[ ]` |
| **V5** | Cloud window: bring up **only** the Polymarket producer. Verify the funnel log line, then query for non-zero prices. | `[ ]` |
| **V6** | One real forecast through the partner frontend against a picked market. Confirm: `tier_1`, `marketProbability` populated, comparison chart rendering, `predictionSeries` non-empty. | `[ ]` |

---

## §4 — Gates & test directives

Tests ship in the same sprint as the code they cover (project rule).

**G1 — The existing REST test must call the real function.** `_payload_from()` in
`tests/test_ingestion/test_polymarket_rest_price.py` rebuilds the payload by hand, so
it validates `_extract_outcome_prices` → mapper but **not** `_fetch_market_prices` →
mapper. Renaming the `price` key in the producer would leave this test green — the
exact class of seam bug being fixed. Refactor the test to invoke
`_fetch_market_prices` itself.

**G2 — T7's payload contract is untested.** The current test asserts the catalog
fields exist on the *Gamma object*, not that the producer emits them. Assert on the
emitted payload.

**G3 — Selection tests.** With a captured multi-event `/events` response: the tag
filter keeps only TARGET events; a past `endDate` is dropped; a market with no
`outcomePrices` is treated as never-traded and skipped without a WARNING storm
(F9 — this is normal, not an error).

**G4 — Silver carry-through.** A REST payload through `map_price_update_to_silver`
yields `metadata_extension` containing all five new keys, `unit == "probability"`,
and `status` taken from the payload.

**G5 — Range guard.** A Polymarket record with `current_value` of `-0.1` or `1.5` routes
to DLQ; a value of exactly `0.0` or `1.0` is **accepted** (both are legitimate market
states); a FRED record with a value of `4.35` is unaffected.

**G6 — Resolution order.** A `conditionId` present in the vault resolves exactly; one
absent from the vault triggers the live path; neither present resolves to refusal, not
to a zero.

---

## §5 — Execution, sequencing, and the cloud path

**Day 1.** Track P and Track S in one Claude Code session (they are the same
deployment unit). Track A's A2 investigation in parallel — it is a read, and its
answer shapes A1. P5/P6/P7 are independent and can be committed first as isolated
commits.

**Day 2.** Track A implementation. Local gate V1 on the pipeline side.

**Day 3.** V2–V6. Cloud deployment and the single end-to-end forecast.

**Do not run a broad `kubectl apply`.** KG-C-10 is live: the committed manifests
declare `replicas: 1` for workloads currently held at 0, so a routine apply silently
restarts the whole ingestion path. All nine producer DAGs are intentionally paused and
must not be reflexively unpaused. Bring up the Polymarket producer specifically, per
`docs/guides/bringup_profiles.md`.

**Cluster state assumption:** the cloud system is up on the AGENTS profile — the agent
is live, the nine producers are paused. This plan requires a targeted Polymarket
window (V5) and an agent rollout (V4), not a full PIPELINE bring-up.

**The wipe follows this work, not the reverse.** Nothing is deleted until V6 passes.

### What this plan deliberately does not do

- **Free-text question→market matching** (D10). The picker path is deterministic; a
  typed question that resolves to nothing refuses honestly.
- **The event/market catalog table.** Still the right architecture, but D1's threshold
  and D7's cadence solve what it was meant to solve. Deferred to post-wipe.
- **WebSocket revival.** `tokens` never existed on Gamma (F11). Note that
  `clob_token_ids` now makes this cheap — `_token_ids` is still built from the
  non-existent `tokens` key and could be fed from the parsed ids in a few lines.
  Not now.
- **The fabricated `momentum` block** (`change_24h/7d/30d`), `bid_ask_spread`,
  `is_divergent`, `resolution_rules`. Burst collection makes these structurally
  meaningless. Recorded so they are not rediscovered.
- **The `/comments` enum discovery** (still "pending discovery" in
  `_comment_poll_loop`). Unanswered by the collaborator; minutes of work for whoever
  is next on the live API.

---

## §6 — Partner track (`client/` + `server/`) — Ron relays

Not ours to implement. Listed so the dependency is explicit and can be sent as one
message.

**PT1 — Align the picker's filters with the collector's.** The picker fetches live
from Gamma ranked by 24h volume across all categories including sports; the collector
takes the six TARGET categories at cumulative volume ≥ $500k. **A user can therefore
pick a market the vault never collected.** Aligning is a few parameters on a call the
partner already makes. Our A3 safety net makes us independent of whether this lands,
but alignment is the clean fix.

**PT2 — Build the price-history chart component.** No time-series component exists in
`client/src/components/cards/`. The data side is already built on ours (A7) and writes
to the `predictionSeries` subcollection. **Send him the exact document shape from A7
before he builds** — otherwise the mismatch surfaces on day 3.

**PT3 — No action expected on the comparison chart.** `MarketComparison.tsx` is
complete and correctly wired. Its only gate is `marketProbability != null`; it renders
"No market benchmark" when that is null, which is exactly what is on screen today. It
lights up the moment a real price arrives — no partner change required.

> **Correction to an earlier reading:** `tier === 'tier_2'` does **not** gate the
> comparison chart; it only changes the wording of the empty state. Tier classification
> still matters for `predictionSeries`, which Sprint 22 writes on the Tier-1 path only
> (A6).

**PT4 — Ask for the collaborator's evidence file.** `polymarket_price_and_coverage.md`
cites `dev/producer-fix-handover.md` as full evidence for its D1–D10; it is not tracked
in the repo. It holds the API-shape record that the post-wipe catalog work needs.

---

## §7 — Carry-over open items from the pre-merge review

Full detail: `Claude-anizai-docs/polymarket/open_items_collaborator_review.md`.

| Item | Status |
|---|---|
| OI-1 — `LOG_INFO_SAMPLE_RATE=1.0` recommended in `Dockerfile.polymarket` | Addressed by **P5 + P6** (D8) |
| OI-2 — `_auth_headers()` apparently removed from the REST path | **CLOSED — false alarm.** The function is alive in `_fetch_active_markets` and `_fetch_market_comments`; the removed diff line was the entire per-market CLOB round-trip, dropped as part of T2 |
| OI-3 — pagination / `whale_alert` absent | **CONFIRMED.** `whale_alert` was done; T5/T6 were not. Addressed by **P1–P3** |
| OI-4 — REST test seam | **CONFIRMED partial.** Addressed by **G1 + G2** |
| OI-5 — `.gitignore` half closed | Addressed by **P7** |
| Plan file not registered in the domain index | The collaborator's plan landed in `plans/` with no cursor or sprint-table entry. Resolve at closeout: adopt, supersede, or archive |

---

## §8 — Skills

- `bugfix` — before/after comparison discipline; the "before" is §0
- `code-review` — four-gate model and test protocol (§4)
- `infrastructure` — Dockerfile / env / image build (P6, V2)
- `flink-ops` — cancel + resubmit of both jobs (V3)
- `frontend-integration` — Firestore write order and status transitions (A7, A8)
