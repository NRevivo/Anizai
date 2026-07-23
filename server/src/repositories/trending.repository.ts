import { logger } from '../lib/logger.js';

/** One outcome of a trending event. For a binary event this is the Yes side; for a
 *  multi-outcome event it is one candidate leg (e.g. "Abiy Ahmed"). */
export interface TrendingOutcome {
    label: string;
    probability: number;
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
    /** 24-hour traded volume, in USD. The value the feed is ranked by. */
    volume24h: number;
    /** Total markets in the event — 1 means binary. */
    marketCount: number;
}

// In-memory cache for the Polymarket fetch. The /trending endpoint is public
// (the landing page hits it unauthenticated), so without a cache every page
// view would fan out to gamma-api.polymarket.com. We keep a single shared
// payload at the largest limit anyone asks for, and slice client-side.
const TTL_MS = 5 * 60 * 1000;
let cache: { fetchedAt: number; limit: number; data: TrendingForecast[] } | null = null;
let inflight: Promise<TrendingForecast[]> | null = null;

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

export const trendingRepository = {
    /**
     * Get trending Polymarket events, ranked by 24-hour traded volume.
     */
    async getTopTrending(limit = 20): Promise<TrendingForecast[]> {
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
