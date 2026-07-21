import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// The repository holds a module-level 5-minute cache, so each test imports a
// fresh copy of the module to avoid one test's cached payload leaking into the
// next.
async function freshRepo() {
    vi.resetModules();
    const mod = await import('../src/repositories/trending.repository.js');
    return mod.trendingRepository;
}

function stubFetch(payload: unknown, ok = true, status = 200) {
    const fetchMock = vi.fn(async (_url: string) => ({
        ok,
        status,
        statusText: ok ? 'OK' : 'Service Unavailable',
        json: async () => payload,
    }));
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
}

const market = (over: Record<string, unknown> = {}) => ({
    closed: false,
    outcomes: '["Yes", "No"]',
    outcomePrices: '["0.93","0.07"]',
    ...over,
});

const binaryEvent = (over: Record<string, unknown> = {}) => ({
    id: '900',
    slug: 'fed-holds-july',
    title: 'Will the Fed hold rates in July?',
    volume24hr: 825180.5,
    tags: [{ label: 'Economy' }],
    markets: [market()],
    ...over,
});

// An on-topic multi-outcome field. Tags matter: a Ballon d'Or fixture would now be
// dropped by the topic filter, which would make the mapping tests below fail for a
// reason unrelated to what they assert.
const multiEvent = (over: Record<string, unknown> = {}) => ({
    id: '901',
    slug: 'presidential-election-winner-2028',
    title: 'Presidential Election Winner 2028',
    volume24hr: 2574433,
    tags: [{ label: 'US Election' }, { label: 'Politics' }, { label: 'Elections' }],
    markets: [
        market({ groupItemTitle: 'JD Vance', outcomePrices: '["0.38","0.62"]' }),
        market({ groupItemTitle: 'Marco Rubio', outcomePrices: '["0.37","0.63"]' }),
        market({ groupItemTitle: 'Gavin Newsom', outcomePrices: '["0.11","0.89"]' }),
        market({ groupItemTitle: 'Josh Shapiro', outcomePrices: '["0.04","0.96"]' }),
    ],
    ...over,
});

describe('trendingRepository.fetchFresh', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it('queries the /events endpoint, not /markets', async () => {
        const fetchMock = stubFetch([binaryEvent()]);

        const repo = await freshRepo();
        await repo.getTopTrending(20);

        const url = String(fetchMock.mock.calls[0][0]);
        expect(url).toContain('gamma-api.polymarket.com/events');
        // /markets returns individual candidate legs rather than event cards (KG-C-13).
        expect(url).not.toContain('/markets');
    });

    it('orders by 24h volume, not lifetime volume', async () => {
        const fetchMock = stubFetch([binaryEvent()]);

        const repo = await freshRepo();
        await repo.getTopTrending(20);

        const url = String(fetchMock.mock.calls[0][0]);
        expect(url).toContain('order=volume24hr');
        expect(url).not.toContain('order=volumeNum');
        expect(url).toContain('ascending=false');
    });

    it('maps a binary event to a single Yes probability', async () => {
        stubFetch([binaryEvent()]);

        const repo = await freshRepo();
        const [row] = await repo.getTopTrending(20);

        expect(row.probability).toBeCloseTo(0.93);
        expect(row.marketCount).toBe(1);
        expect(row.outcomes).toEqual([{ label: 'Yes', probability: 0.93 }]);
        expect(row.volume24h).toBe(825180.5);
        expect(row.url).toBe('https://polymarket.com/event/fed-holds-july');
    });

    it('maps a multi-outcome event to null probability and top outcomes', async () => {
        stubFetch([multiEvent()]);

        const repo = await freshRepo();
        const [row] = await repo.getTopTrending(20);

        // A 4-candidate field has no single probability — see the type comment.
        expect(row.probability).toBeNull();
        expect(row.marketCount).toBe(4);
        // Price-descending, capped at the top 3.
        expect(row.outcomes.map((o) => o.label)).toEqual([
            'JD Vance',
            'Marco Rubio',
            'Gavin Newsom',
        ]);
        expect(row.outcomes[0].probability).toBeCloseTo(0.38);
    });

    it('collapses outcomes that would render as the same percentage', async () => {
        // A strike ladder: BTC is far above the low strikes, so six legs sit at 100%.
        // Without deduping, the top-3 is three identical rows carrying one bit.
        stubFetch([
            multiEvent({
                title: 'Bitcoin above ___ on July 20?',
                tags: [{ label: 'Bitcoin' }, { label: 'Crypto' }],
                markets: [
                    market({ groupItemTitle: '58,000', outcomePrices: '["1.0","0.0"]' }),
                    market({ groupItemTitle: '60,000', outcomePrices: '["1.0","0.0"]' }),
                    market({ groupItemTitle: '62,000', outcomePrices: '["1.0","0.0"]' }),
                    market({ groupItemTitle: '64,000', outcomePrices: '["0.62","0.38"]' }),
                    market({ groupItemTitle: '66,000', outcomePrices: '["0.001","0.999"]' }),
                ],
            }),
        ]);

        const repo = await freshRepo();
        const [row] = await repo.getTopTrending(20);

        // One representative per rendered percentage, highest price first.
        expect(row.outcomes.map((o) => o.label)).toEqual(['58,000', '64,000', '66,000']);
        // marketCount still reports the true size of the field.
        expect(row.marketCount).toBe(5);
    });

    it('keeps distinct outcomes in a candidate field untouched', async () => {
        stubFetch([multiEvent()]);

        const repo = await freshRepo();
        const [row] = await repo.getTopTrending(20);

        expect(row.outcomes.map((o) => Math.round(o.probability * 100))).toEqual([38, 37, 11]);
    });

    it('reads the Yes price by label rather than by position', async () => {
        // Reversed outcome order: index 0 is No. Reading positionally would report
        // the complement — a 7% market as 93%.
        stubFetch([
            binaryEvent({
                markets: [
                    market({ outcomes: '["No", "Yes"]', outcomePrices: '["0.93","0.07"]' }),
                ],
            }),
        ]);

        const repo = await freshRepo();
        const [row] = await repo.getTopTrending(20);

        expect(row.probability).toBeCloseTo(0.07);
    });

    it('excludes single-fixture events tagged Games', async () => {
        stubFetch([
            {
                id: '902',
                slug: 'lol-drx-vs-fearx',
                title: 'LoL: Kiwoom DRX vs BNK FEARX (BO1) - KeSPA Cup',
                volume24hr: 974653,
                tags: [{ label: 'Esports' }, { label: 'Games' }, { label: 'Sports' }],
                markets: [market()],
            },
            binaryEvent(),
        ]);

        const repo = await freshRepo();
        const rows = await repo.getTopTrending(20);

        expect(rows).toHaveLength(1);
        expect(rows[0].title).toBe('Will the Fed hold rates in July?');
    });

    // --- KG-C-15: topic filter ---

    it('keeps an event whose tag maps to a covered pipeline topic', async () => {
        // 'Economic Policy' / 'Fed Rates' -> Financial & Monetary Policy. The pipeline's
        // own vocabulary says "federal reserve"/"rate cut", so matching its keywords
        // against this text would drop it — the tag map exists for exactly this case.
        stubFetch([
            binaryEvent({
                title: 'Fed Decision in July?',
                tags: [{ label: 'Economic Policy' }, { label: 'Fed Rates' }, { label: 'fomc' }],
            }),
        ]);

        const repo = await freshRepo();
        const rows = await repo.getTopTrending(20);

        expect(rows).toHaveLength(1);
        expect(rows[0].title).toBe('Fed Decision in July?');
    });

    it('drops an event whose tags map to no covered topic', async () => {
        stubFetch([
            binaryEvent({
                title: 'Highest grossing movie in 2026?',
                tags: [{ label: 'The Odyssey' }, { label: 'box office' }],
            }),
            binaryEvent(),
        ]);

        const repo = await freshRepo();
        const rows = await repo.getTopTrending(20);

        expect(rows.map((r) => r.title)).toEqual(['Will the Fed hold rates in July?']);
    });

    it('drops season-long sports events — the pipeline ingests no sport sources', async () => {
        // Tagged Sports but NOT Games, so the fixture filter alone would keep it.
        stubFetch([
            multiEvent({
                title: 'NBA: LeBron James Next Team',
                tags: [{ label: 'NBA' }, { label: 'Sports' }, { label: 'Basketball' }],
            }),
        ]);

        const repo = await freshRepo();

        expect(await repo.getTopTrending(20)).toHaveLength(0);
    });

    it('lets an excluded category beat an otherwise-covered tag', async () => {
        // 'Trump' maps to Elections, but this is a World Cup photo-op market. Exclusion
        // must be checked first or novelty markets ride in on a political tag.
        stubFetch([
            binaryEvent({
                title: 'Will Trump be in the WC Champions Photo?',
                tags: [{ label: 'Trump' }, { label: 'Soccer' }, { label: 'Sports' }],
            }),
        ]);

        const repo = await freshRepo();

        expect(await repo.getTopTrending(20)).toHaveLength(0);
    });

    it('drops tweet-count markets despite their Politics tag', async () => {
        stubFetch([
            binaryEvent({
                title: 'Elon Musk # tweets July 14 - July 21, 2026?',
                tags: [{ label: 'Culture' }, { label: 'Politics' }, { label: 'Tweet Markets' }],
            }),
        ]);

        const repo = await freshRepo();

        expect(await repo.getTopTrending(20)).toHaveLength(0);
    });

    it('returns empty rather than backfilling when nothing is on topic', async () => {
        // KG-C-11's rule applied to the topic filter: never pad with off-topic events.
        stubFetch([
            binaryEvent({ title: 'NFL Champion 2027', tags: [{ label: 'NFL' }] }),
            binaryEvent({ title: 'Highest temperature in Seoul?', tags: [{ label: 'Weather' }] }),
        ]);

        const repo = await freshRepo();

        expect(await repo.getTopTrending(20)).toEqual([]);
    });

    it('drops resolved legs from an events market list', async () => {
        stubFetch([
            multiEvent({
                markets: [
                    market({ groupItemTitle: 'Settled Guy', closed: true }),
                    market({ groupItemTitle: 'Live Guy', outcomePrices: '["0.42","0.58"]' }),
                    market({ groupItemTitle: 'Other Guy', outcomePrices: '["0.10","0.90"]' }),
                ],
            }),
        ]);

        const repo = await freshRepo();
        const [row] = await repo.getTopTrending(20);

        expect(row.marketCount).toBe(2);
        expect(row.outcomes.map((o) => o.label)).toEqual(['Live Guy', 'Other Guy']);
    });

    // KG-C-11 — the regression this guards. The old implementation caught the
    // failure and served seeded Firestore documents, so a dead upstream looked
    // like a healthy feed. It must now surface as an error.
    it('rejects instead of substituting fallback data when Polymarket fails', async () => {
        stubFetch([], false, 503);

        const repo = await freshRepo();

        await expect(repo.getTopTrending(20)).rejects.toThrow(/Polymarket API error: 503/);
    });

    it('propagates a network-level failure rather than swallowing it', async () => {
        vi.stubGlobal(
            'fetch',
            vi.fn(async () => {
                throw new Error('ECONNREFUSED');
            })
        );

        const repo = await freshRepo();

        await expect(repo.getTopTrending(20)).rejects.toThrow(/ECONNREFUSED/);
    });
});
