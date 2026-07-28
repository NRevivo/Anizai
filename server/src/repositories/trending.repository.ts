import { logger } from '../lib/logger.js';

/** One outcome of a trending event. For a binary event this is the Yes side; for a
 *  multi-outcome event it is one candidate leg (e.g. "Abiy Ahmed"). */
export interface TrendingOutcome {
    label: string;
    probability: number;
}

/**
 * One selectable market inside an event.
 *
 * This is the unit a forecast is actually created against. `outcomes` above is a
 * *display* summary (deduped, capped at three); `markets` is the *complete*
 * selectable field, because the user picks one of these and we submit its
 * `question` verbatim plus its `conditionId`.
 *
 * Why both `question` and `groupItemTitle`: the picker shows the short label but
 * must submit the full question. Submitting the label ("Abiy Ahmed") or the event
 * title ("Next Prime Minister of Ethiopia?") is what makes the pipeline's
 * question-matching miss — only `question` is the text the vault stores.
 */
export interface TrendingMarket {
    /**
     * Polymarket condition id. This is the exact value the pipeline stores as
     * `momentum_vault.external_reference_id` for REST-snapshot rows, so it is the
     * deterministic join key between a trending card and a vault row.
     */
    conditionId: string;
    /** The real market question — submitted verbatim as the forecast question. */
    question: string;
    /**
     * Short leg label for a candidate field ("Abiy Ahmed"). Binary events carry no
     * `groupItemTitle`, so this falls back to `question` and the picker stays
     * renderable without a second null check.
     */
    groupItemTitle: string;
    /** Yes-side probability, 0–1. */
    probability: number;
    /** 24-hour traded volume for this individual market, in USD. */
    volume24h: number;
}

export interface TrendingForecast {
    /** Polymarket event id. */
    id: string;
    title: string;
    /** Canonical Polymarket URL for the event. */
    url: string;
    /**
     * Yes probability for a binary event (single market). `null` for a
     * multi-outcome event — a 89-candidate field has no single probability, which
     * is why Polymarket's own card shows the leading outcomes instead. Consumers
     * must branch on this rather than defaulting it to a number.
     */
    probability: number | null;
    /** Binary: a single Yes entry. Multi-outcome: the leading legs, price-desc. */
    outcomes: TrendingOutcome[];
    /**
     * Selectable markets, probability-descending. No dedup and no cap — unlike
     * `outcomes`, this is the real field a picker chooses from.
     *
     * ⚠️ **CONDITIONALLY POPULATED on the list response. Empty there means "not
     * loaded at this layer", NEVER "this event has no markets".** `forListResponse`
     * keeps it inline for binary events (`marketCount === 1`) and strips it for
     * multi-outcome ones, which are served by `GET /trending/:id/markets` instead —
     * the list is public and unauthenticated, and shipping every field to every
     * visitor cost 59.9 KB against 4.5 KB. Internally (`getTopTrendingFull`, the
     * cache) it is always fully populated.
     *
     * ⚠️ `markets.length` is also normally SMALLER than `marketCount`: inactive
     * placeholder legs are excluded here but still counted there (a 33-market
     * Ethiopia field yields 8 selectable markets). Render counts from
     * `markets.length`, not `marketCount`, anywhere the number describes what the
     * user can actually choose.
     */
    markets: TrendingMarket[];
    /** 24-hour traded volume, in USD. The value the feed is ranked by. */
    volume24h: number;
    /** Total non-closed markets in the event — 1 means binary. Includes inactive
     *  placeholder legs, so see the warning on `markets` before displaying it. */
    marketCount: number;
    /**
     * True when the event's outcomes are mutually exclusive — a candidate field
     * ("Next Prime Minister of Ethiopia?", exactly one leg can resolve Yes).
     * False when they are independent, overlapping propositions — the "ladder"
     * shape ("Bitcoin above ___ on July 27?", "…ceasefire continues through…?"),
     * where several legs can resolve Yes together.
     *
     * Read from Polymarket's own `negRisk` flag, not inferred from the title.
     * Validated against 100 live events: `negRisk` is uniform across the markets
     * of all 93 multi-market events, and cleanly separates candidate fields
     * (Ethiopia PM, Ballon d'Or, Fed Decision, party nominees) from ladders
     * (Bitcoin strike ladders, date-series ceasefire markets).
     *
     * Used only to word the picker — both shapes are still a flat list to choose
     * from. Binary events are always false and never reach a picker.
     */
    mutuallyExclusive: boolean;
}

// In-memory cache for the Polymarket fetch. The /trending endpoint is public
// (the landing page hits it unauthenticated), so without a cache every page
// view would fan out to gamma-api.polymarket.com. We keep a single shared
// payload at the largest limit anyone asks for, and slice client-side.
const TTL_MS = 5 * 60 * 1000;
let cache: { fetchedAt: number; limit: number; data: TrendingForecast[] } | null = null;
let inflight: Promise<TrendingForecast[]> | null = null;

// Search is keyed by query, so it cannot share the single-payload cache above.
// A short TTL and a hard entry cap keep repeat/typo queries off the upstream API
// without letting an unbounded map grow from user input.
const SEARCH_TTL_MS = 2 * 60 * 1000;
const SEARCH_CACHE_MAX = 50;
const searchCache = new Map<string, { fetchedAt: number; data: TrendingForecast[] }>();

/** Below this, a query matches too much to be useful and just burns API calls. */
const MIN_QUERY_LENGTH = 2;

/** How many leading legs to carry for a multi-outcome event. */
const TOP_OUTCOMES = 3;

/**
 * The topic domains our pipeline ingests sources for.
 *
 * ⚠ MIRROR — the canonical definition is `MASTER_KEYWORD_LIST` in
 * `data-pipeline/processing/keyword_sniper.py`, whose section comments group ~188
 * keywords into exactly these 13 domains. That list is Domain A (read-only for us)
 * and there is no shared config artifact to read at runtime — `data-pipeline/config/`
 * is pure Python — so this is a deliberate duplicate, in the same spirit as
 * `ingestion/newsapi_producer.GENERAL_KEYWORDS`, which mirrors a subset of the same
 * list and carries the same "update BOTH files" note. The pipeline forbids importing
 * that module across service boundaries (Service Isolation), so a runtime read would
 * be wrong even if the languages matched.
 *
 * Only the *domain names* are mirrored, never the keywords. Measured against 100 live
 * events, mirroring all 188 keywords admitted 47 of 50 eligible events versus 49 for
 * the tag map below alone — the keyword half is where the churn lives (Phase 7B
 * deleted ~10 terms as "too broad") and it buys almost nothing.
 *
 * If Ron adds or removes a *domain*, update this union and TAG_TOPICS together.
 */
type PipelineTopic =
    | 'Geopolitical Conflict & Security'
    | 'Missile & Nuclear'
    | 'Regional Hotspots'
    | 'Energy & Commodities'
    | 'Prediction Markets & Betting'
    | 'AI & Machine Learning'
    | 'Crypto & Digital Assets'
    | 'Startups, VC & Corporate'
    | 'Elections & Government Policy'
    | 'Health, Science & Space'
    | 'Financial & Monetary Policy'
    | 'Technology & Regulatory Policy'
    | 'Scientific / Systemic Risk';

/**
 * Categories the pipeline ingests no sources for. Checked FIRST and unconditionally:
 * a match here drops the event even if another tag would otherwise admit it. That
 * ordering is load-bearing — "Will Trump be in the WC Champions Photo?" carries both
 * `Trump` (admitted below) and `Soccer`, and the Elon-Musk tweet-count markets carry
 * both `Politics` and `Tweet Markets`. Without exclusion-first, both slip through.
 *
 * `games` additionally does the single-fixture job it was added for: Polymarket tags
 * an event `Games` when it is one scheduled match — an esports game ("LoL: Kiwoom DRX
 * vs BNK FEARX (BO1)") or one tennis match ("Estoril Open: Droguet vs Carabelli").
 * Verified against 60 live events: it dropped all 18 individual fixtures while
 * retaining every season-long or award market, including tag-identical ones —
 * "NBA: LeBron James Next Team" and "F1 Drivers' Champion" carry `Sports` but not
 * `Games`. (Those two are now dropped anyway, by `sports`.)
 */
const EXCLUDED_TAGS = new Set([
    // Sport — no producer covers fixtures, standings or player movement.
    'games', 'sports', 'soccer', 'nba', 'mlb', 'nfl', 'nhl', 'tennis', 'atp', 'wta',
    'basketball', 'baseball', 'football', 'cycling', 'golf', 'boxing', 'ufc', 'mma',
    'f1', 'formula 1', 'esports',
    // Entertainment / novelty — no producer covers box office, awards or celebrity.
    'movies', 'box office', 'awards', 'music', 'celebrity', 'pop culture',
    'tweet markets',
    // Weather — OpenWeather feeds forecast *context*, not daily-temperature betting.
    'weather', 'daily temperature', 'highest temperature',
]);

/**
 * Polymarket tag → pipeline topic.
 *
 * This map exists because the two vocabularies genuinely differ. Polymarket labels
 * monetary policy `Fed` / `Fed Rates` / `fomc` / `Jerome Powell`; the sniper says
 * "federal reserve" / "rate cut" / "monetary policy". Matching the pipeline's keywords
 * against event text therefore dropped BOTH "Fed Decision in July?" and "Fed Decision
 * in September?" — a clear false negative, since FRED is one of the nine producers.
 *
 * Unlike the topic list above, this map is ours: it describes Polymarket's taxonomy,
 * not Ron's, so it does not drift when the pipeline retunes its keywords.
 */
const TAG_TOPICS: Record<string, PipelineTopic> = {
    // --- Financial & Monetary Policy (FRED producer) ---
    economy: 'Financial & Monetary Policy',
    'economic policy': 'Financial & Monetary Policy',
    fed: 'Financial & Monetary Policy',
    'fed rates': 'Financial & Monetary Policy',
    fomc: 'Financial & Monetary Policy',
    'jerome powell': 'Financial & Monetary Policy',
    finance: 'Financial & Monetary Policy',
    inflation: 'Financial & Monetary Policy',
    recession: 'Financial & Monetary Policy',

    // --- Elections & Government Policy ---
    politics: 'Elections & Government Policy',
    elections: 'Elections & Government Policy',
    'global elections': 'Elections & Government Policy',
    'main election': 'Elections & Government Policy',
    'us election': 'Elections & Government Policy',
    'world elections': 'Elections & Government Policy',
    primaries: 'Elections & Government Policy',
    // `trump` is a political-outcome tag here ("Trump out as President by July 31?").
    // It is also a celebrity tag, which is why EXCLUDED_TAGS must win — see above.
    trump: 'Elections & Government Policy',

    // --- Geopolitical Conflict & Security ---
    geopolitics: 'Geopolitical Conflict & Security',
    world: 'Geopolitical Conflict & Security',
    'military strikes': 'Geopolitical Conflict & Security',
    'peace deal': 'Geopolitical Conflict & Security',
    ceasefire: 'Geopolitical Conflict & Security',
    war: 'Geopolitical Conflict & Security',

    // --- Regional Hotspots (mirrors the sniper's high-signal geographies) ---
    iran: 'Regional Hotspots',
    israel: 'Regional Hotspots',
    ukraine: 'Regional Hotspots',
    russia: 'Regional Hotspots',
    china: 'Regional Hotspots',
    taiwan: 'Regional Hotspots',
    'north korea': 'Regional Hotspots',
    'middle east': 'Regional Hotspots',
    'united states': 'Regional Hotspots',

    // --- Remaining domains ---
    crypto: 'Crypto & Digital Assets',
    'crypto prices': 'Crypto & Digital Assets',
    bitcoin: 'Crypto & Digital Assets',
    ethereum: 'Crypto & Digital Assets',
    oil: 'Energy & Commodities',
    energy: 'Energy & Commodities',
    tech: 'Technology & Regulatory Policy',
    business: 'Startups, VC & Corporate',
    ai: 'AI & Machine Learning',
    science: 'Health, Science & Space',
    health: 'Health, Science & Space',
};

type Classification =
    | { kept: true; topics: PipelineTopic[] }
    | { kept: false; excludedBy: string | null; tags: string[] };

/** Decide whether an event is on a topic the pipeline can actually forecast. */
function classifyEvent(event: any): Classification {
    const tags = (event?.tags ?? []).map((t: any) => String(t?.label ?? '').toLowerCase());

    const excludedBy = tags.find((t: string) => EXCLUDED_TAGS.has(t)) ?? null;
    if (excludedBy) return { kept: false, excludedBy, tags };

    const topics = [...new Set(tags.map((t: string) => TAG_TOPICS[t]).filter(Boolean))] as PipelineTopic[];
    if (topics.length === 0) return { kept: false, excludedBy: null, tags };

    return { kept: true, topics };
}

function parseJsonArray(raw: unknown): unknown[] {
    if (Array.isArray(raw)) return raw;
    if (typeof raw !== 'string') return [];
    try {
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

/**
 * Price of the "Yes" side of a binary market. Looks the label up rather than
 * assuming index 0 — the outcome order is not guaranteed, and reading the wrong
 * index silently reports the complement (a 3% market as 97%).
 */
function yesPrice(market: any): number | null {
    const labels = parseJsonArray(market?.outcomes).map((o) => String(o).toLowerCase());
    const prices = parseJsonArray(market?.outcomePrices).map((p) => Number(p));
    if (prices.length === 0) return null;

    const yesIndex = labels.indexOf('yes');
    const price = yesIndex >= 0 ? prices[yesIndex] : prices[0];
    return Number.isFinite(price) ? price : null;
}

/**
 * Build the complete selectable field for an event, probability-descending.
 *
 * Filters, and why each one is safe:
 *  - `active !== false` drops unnamed placeholder legs ("Will Person C be the next
 *    Prime Minister of Ethiopia?"). Measured against 100 live events: every active
 *    market carries a price and every price-less market is inactive — 0 exceptions
 *    — so this removes 521 of 1,039 non-closed markets without dropping a single
 *    market a user could meaningfully pick. Note `outcomes` above does NOT apply
 *    this filter; it is a display summary and is left exactly as it was.
 *  - a null `yesPrice` or an empty `conditionId` is defensive only (neither occurs
 *    in the live sample). A market with no identifier cannot serve as a benchmark,
 *    which is the entire reason this array exists.
 */
function toTrendingMarkets(markets: any[]): TrendingMarket[] {
    return markets
        .filter((m: any) => m?.active !== false)
        .map((m: any): TrendingMarket | null => {
            const probability = yesPrice(m);
            const conditionId = String(m?.conditionId ?? '').trim();
            const question = String(m?.question ?? '').trim();
            if (probability === null || !conditionId || !question) return null;
            return {
                conditionId,
                question,
                groupItemTitle: String(m?.groupItemTitle || '').trim() || question,
                probability,
                volume24h: Number(m?.volume24hr ?? 0) || 0,
            };
        })
        .filter((m: TrendingMarket | null): m is TrendingMarket => m !== null)
        .sort((a: TrendingMarket, b: TrendingMarket) => b.probability - a.probability);
}

function toTrendingForecast(event: any): TrendingForecast | null {
    // Legs can resolve while the parent event is still open; a settled leg would
    // otherwise show as a 0% or 100% outcome.
    const markets = (event?.markets ?? []).filter((m: any) => m?.closed !== true);
    if (markets.length === 0) return null;

    const slug = String(event?.slug ?? '');
    const base = {
        id: String(event?.id ?? ''),
        title: String(event?.title ?? ''),
        url: slug ? `https://polymarket.com/event/${slug}` : 'https://polymarket.com',
        volume24h: Number(event?.volume24hr ?? 0) || 0,
        marketCount: markets.length,
        markets: toTrendingMarkets(markets),
        // Read from the markets rather than the event: event-level `negRisk` is
        // absent on binary events, while the market-level flag is always present
        // and uniform within an event (verified, 93/93 multi-market events).
        mutuallyExclusive:
            markets.length > 1 && markets.every((m: any) => m?.negRisk === true),
    };

    if (markets.length === 1) {
        const probability = yesPrice(markets[0]);
        if (probability === null) return null;
        return {
            ...base,
            probability,
            outcomes: [{ label: 'Yes', probability }],
        };
    }

    const ranked = markets
        .map((m: any) => {
            const probability = yesPrice(m);
            if (probability === null) return null;
            return {
                label: String(m?.groupItemTitle || m?.question || '').trim(),
                probability,
            };
        })
        .filter((o: TrendingOutcome | null): o is TrendingOutcome => o !== null && o.label !== '')
        .sort((a: TrendingOutcome, b: TrendingOutcome) => b.probability - a.probability);

    // Never spend two of the three slots on outcomes that render identically.
    // Strike ladders make this acute: "Bitcoin above ___ on July 20?" has six legs
    // at 100% (BTC is far above every one of them), so a plain price-desc top-3 reads
    // "62,000 100% · 60,000 100% · 58,000 100%" — three rows carrying one bit between
    // them. Deduping on the *rendered* percentage surfaces where the market actually
    // turns over instead. Candidate fields are unaffected, their prices differ.
    const seenPercent = new Set<number>();
    const outcomes = ranked
        .filter((o: TrendingOutcome) => {
            const percent = Math.round(o.probability * 100);
            if (seenPercent.has(percent)) return false;
            seenPercent.add(percent);
            return true;
        })
        .slice(0, TOP_OUTCOMES);

    if (outcomes.length === 0) return null;

    // No single probability exists for a candidate field — see the type comment.
    return { ...base, probability: null, outcomes };
}

/**
 * Project a cached forecast down to what the LIST response ships.
 *
 * The cache holds every event's complete `markets` array — memory is not the
 * constraint, wire bytes are. A multi-outcome field is dropped here and fetched
 * per-event from `GET /trending/:id/markets` only once the user actually opens a
 * picker. Measured on `?limit=12`: 59.9 KB → 4.5 KB.
 *
 * Binary events keep their single market inline. It costs ~156 bytes across the
 * whole page and it is what lets a binary card submit on click with no second
 * round-trip — that path has no picker to show a spinner in.
 *
 * Returns a shallow copy: mutating the cached objects would empty the cache the
 * detail endpoint reads from.
 */
function forListResponse(forecast: TrendingForecast): TrendingForecast {
    if (forecast.marketCount === 1) return forecast;
    return { ...forecast, markets: [] };
}

export const trendingRepository = {
    /**
     * Get trending Polymarket events, ranked by 24-hour traded volume.
     *
     * `markets` is stripped for multi-outcome events — see `forListResponse`.
     */
    async getTopTrending(limit = 20): Promise<TrendingForecast[]> {
        const data = await trendingRepository.getTopTrendingFull(limit);
        return data.map(forListResponse);
    },

    /**
     * The cached forecasts with `markets` intact. Internal — the HTTP list route
     * must go through `getTopTrending`, which strips them.
     */
    async getTopTrendingFull(limit = 20): Promise<TrendingForecast[]> {
        const now = Date.now();
        if (cache && now - cache.fetchedAt < TTL_MS && cache.limit >= limit) {
            return cache.data.slice(0, limit);
        }
        if (inflight) {
            const data = await inflight;
            return data.slice(0, limit);
        }
        inflight = trendingRepository.fetchFresh(Math.max(limit, 20));
        try {
            const data = await inflight;
            cache = { fetchedAt: Date.now(), limit: Math.max(limit, 20), data };
            return data.slice(0, limit);
        } finally {
            inflight = null;
        }
    },

    /**
     * Search Polymarket events by free text, same shape as the trending list.
     *
     * Why this exists: `/trending` can only ever see Gamma's first 100 events by
     * 24h volume, of which roughly half survive the topic filter. Anything outside
     * that slice — most of a ~2,100-market catalogue — is unreachable by browsing
     * alone, including the Fed markets this feature was asked for. `/public-search`
     * queries the whole catalogue.
     *
     * Results are put through the **same** `classifyEvent` topic filter as the
     * trending feed, so search can only surface events the pipeline ingests sources
     * for. A sport or entertainment market is therefore not findable here — that is
     * deliberate, not a gap: forecasting one would produce a confident-looking answer
     * with no evidence behind it.
     *
     * The upstream payload is field-identical to `/events`, so `toTrendingForecast`
     * and `toTrendingMarkets` are reused verbatim — there is no second mapper to
     * keep in sync. `forListResponse` applies too, so multi-outcome fields are
     * stripped here and fetched per-event by the picker exactly as on the list.
     */
    async searchEvents(query: string, limit = 20): Promise<TrendingForecast[]> {
        const normalized = query.trim().toLowerCase();
        if (normalized.length < MIN_QUERY_LENGTH) {
            return [];
        }

        const cacheKey = `${normalized}::${limit}`;
        const hit = searchCache.get(cacheKey);
        if (hit && Date.now() - hit.fetchedAt < SEARCH_TTL_MS) {
            return hit.data.map(forListResponse);
        }

        // Over-fetch: the topic filter drops a large share of any result page, and
        // the caller still expects up to `limit` rows.
        const url =
            `https://gamma-api.polymarket.com/public-search` +
            `?q=${encodeURIComponent(query.trim())}` +
            `&limit_per_type=${Math.min(limit * 3, 60)}`;

        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(
                `Polymarket API error: ${response.status} ${response.statusText}`
            );
        }

        const body = (await response.json()) as { events?: unknown };
        const events = Array.isArray(body?.events) ? body.events : [];

        const rows = events
            .filter((event: any) => event?.closed !== true && classifyEvent(event).kept)
            .map(toTrendingForecast)
            .filter((row): row is TrendingForecast => row !== null)
            .slice(0, limit);

        if (searchCache.size >= SEARCH_CACHE_MAX) {
            // Map preserves insertion order, so the first key is the oldest.
            const oldest = searchCache.keys().next().value;
            if (oldest !== undefined) searchCache.delete(oldest);
        }
        searchCache.set(cacheKey, { fetchedAt: Date.now(), data: rows });

        return rows.map(forListResponse);
    },

    /**
     * Every selectable market for one event, probability-descending.
     *
     * Served from the same in-process cache the list route uses, so the common
     * case — user clicks a card they can see, within the 5-minute TTL — costs no
     * upstream call at all.
     *
     * Falls back to a single-event Gamma fetch when the cache cannot answer. Two
     * real cases: the TTL lapsed while the page sat open, and the event dropped
     * out of the top-N on a refresh because the feed is ranked by 24h volume.
     * Without the fallback both would 404 a card the user is looking at.
     *
     * The fallback deliberately skips the topic/exclusion classifier that
     * `fetchFresh` applies. The event was already admitted by that filter when it
     * was rendered; re-checking it here could only reject a card the user has
     * legitimately clicked.
     *
     * Returns null when the event genuinely does not exist upstream (→ 404).
     */
    async getEventMarkets(eventId: string): Promise<TrendingMarket[] | null> {
        const now = Date.now();
        if (cache && now - cache.fetchedAt < TTL_MS) {
            const hit = cache.data.find((e) => e.id === eventId);
            if (hit) return hit.markets;
        }

        // Also serve from the search cache: an event opened from a search result is
        // not in the trending payload, and without this every such pick would pay an
        // upstream round-trip for markets we already hold.
        for (const entry of searchCache.values()) {
            if (now - entry.fetchedAt >= SEARCH_TTL_MS) continue;
            const hit = entry.data.find((e) => e.id === eventId);
            if (hit) return hit.markets;
        }

        const url = `https://gamma-api.polymarket.com/events?id=${encodeURIComponent(eventId)}`;
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(
                `Polymarket API error: ${response.status} ${response.statusText}`
            );
        }

        const body = (await response.json()) as unknown;
        const events = Array.isArray(body) ? body : [body];
        const event = events[0] as any;
        if (!event || String(event?.id ?? '') !== eventId) return null;

        // Same closed-leg filter the list path applies (see toTrendingForecast).
        const markets = (event?.markets ?? []).filter((m: any) => m?.closed !== true);
        return toTrendingMarkets(markets);
    },

    async fetchFresh(limit: number): Promise<TrendingForecast[]> {
        // Query /events, not /markets. `/markets` returns the individual binary
        // legs of an event, so a single candidate ("Will Lionel Messi win the 2026
        // Ballon d'Or?") surfaces as if it were a top-level question instead of the
        // "Ballon d'Or Winner 2026" card a user recognises. `/events` is what
        // Polymarket's own UI renders. (KG-C-13)
        //
        // Order by 24-hour volume, NOT lifetime `volume`: lifetime ranks by
        // "biggest market ever" and fills the feed with long-dead novelty markets.
        // (KG-C-12)
        //
        // Over-fetch, because the noise and topic filters below drop roughly half the
        // page and we still owe the caller `limit` rows. 100 is the API's ceiling, so
        // a caller asking for a very large limit may receive fewer rows than requested
        // — acceptable: returning fewer on-topic events beats padding with off-topic
        // ones.
        const fetchCount = Math.min(limit * 4, 100);
        const url =
            `https://gamma-api.polymarket.com/events` +
            `?limit=${fetchCount}&active=true&closed=false&order=volume24hr&ascending=false`;

        const response = await fetch(url);

        if (!response.ok) {
            // Propagate: the route's error handler turns this into a 500 and the
            // client degrades to its empty state. Never substitute fabricated or
            // seeded data for a live feed (KG-C-11).
            throw new Error(
                `Polymarket API error: ${response.status} ${response.statusText}`
            );
        }

        const events = (await response.json()) as any[];

        const onTopic: any[] = [];
        const unmappedTags = new Set<string>();

        for (const event of events) {
            const verdict = classifyEvent(event);
            if (verdict.kept) {
                onTopic.push(event);
                continue;
            }
            // An event dropped for having no recognised tag is the interesting case:
            // it may be a Polymarket category we simply have not mapped, and the
            // failure mode is silent coverage loss. Surface the tags so the gap is
            // discoverable rather than invisible. (Excluded-by-category drops are
            // expected and uninteresting, so they are not collected.)
            if (verdict.excludedBy === null) {
                verdict.tags.forEach((t) => unmappedTags.add(t));
            }
        }

        if (unmappedTags.size > 0) {
            logger.debug(
                { unmappedTags: [...unmappedTags] },
                '[trending] events dropped with no tag mapped to a pipeline topic'
            );
        }

        const rows = onTopic
            .map(toTrendingForecast)
            .filter((e): e is TrendingForecast => e !== null)
            .slice(0, limit);

        if (rows.length === 0) {
            // Return the empty result rather than relaxing the filter. Backfilling
            // with off-topic events would be the same class of defect as the seeded
            // Firestore fallback removed in KG-C-11: a panel that looks healthy while
            // showing things we cannot forecast.
            logger.warn(
                { fetched: events.length },
                '[trending] no fetched event matched a pipeline topic; returning empty'
            );
        }

        return rows;
    },
};
