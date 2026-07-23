/**
 * Seed a single fully-populated forecast session for UI development.
 *
 * Writes a genuine Firestore session (sessions/{id} + sessionResults/{id} +
 * evidence / sentimentTimeSeries / agentEvents subcollections) so the
 * dashboard renders it through Firestore -> Express -> React exactly like
 * Ron's pipeline output would.
 *
 * Run with:  cd server && npm run seed
 *
 * Idempotent: uses fixed document IDs, so re-running overwrites in place.
 * Remove it with:  npm run seed:clean -- --yes
 */

import 'dotenv/config';
import { initializeApp, applicationDefault } from 'firebase-admin/app';
import { getFirestore, Timestamp } from 'firebase-admin/firestore';

initializeApp({
    credential: applicationDefault(),
    projectId: process.env.FIREBASE_PROJECT_ID || 'anizai-ai',
});

const db = getFirestore();

// ── Owner ────────────────────────────────────────────────────────
const ADMIN_USER_ID = 'gEnzUuBLpcNwITpow33AqEnfFCs1';

// ── Identity ─────────────────────────────────────────────────────
const SESSION_ID = 'seed-recession-2026';
const QUESTION = 'Will the US enter a recession in 2026?';
const TITLE = '[SEED] Will the US enter a recession in 2026?';

// ── Time helpers ─────────────────────────────────────────────────
const NOW = Date.now();
const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
const tsFromOffset = (msAgo: number) => Timestamp.fromMillis(NOW - msAgo);

const createdAt = tsFromOffset(2 * HOUR);
const generatedAt = tsFromOffset(1 * HOUR);
const updatedAt = tsFromOffset(0);

// ── Evidence (10 items) ──────────────────────────────────────────
// IDs are generated first so keyFactors can reference them.
type SeedEvidence = {
    id: string;
    type: 'news' | 'expert' | 'social' | 'market';
    sourceType: string;
    title: string;
    snippet: string;
    url: string;
    source: string;
    sourceDomain: string;
    relevanceScore: number;
    credibilityTier: 'tier_1' | 'tier_2' | 'tier_3';
    recencyWeight: number;
    impactOnForecast: 'positive' | 'negative' | 'neutral';
    impactMagnitude: number;
    usedInAnswer: boolean;
    justification: string;
    isKeyEvidence: boolean;
    rank: number;
    publishedDaysAgo: number;
};

const EVIDENCE: SeedEvidence[] = [
    {
        id: 'seed-ev-1',
        type: 'news',
        sourceType: 'online_news',
        title: 'US labor market cools as jobless claims climb to a two-year high',
        snippet:
            'Initial unemployment claims rose for the fourth consecutive week, the longest streak since the 2023 slowdown. Economists warn that a softening labor market is often the clearest early signal of a coming downturn.',
        url: 'https://www.bloomberg.com/news/articles/2026-05-12/us-jobless-claims-climb-recession-signal',
        source: 'Bloomberg',
        sourceDomain: 'bloomberg.com',
        relevanceScore: 0.92,
        credibilityTier: 'tier_1',
        recencyWeight: 0.98,
        impactOnForecast: 'positive',
        impactMagnitude: 0.85,
        usedInAnswer: true,
        justification: 'Rising jobless claims are a leading indicator that directly raises recession odds.',
        isKeyEvidence: true,
        rank: 1,
        publishedDaysAgo: 2,
    },
    {
        id: 'seed-ev-2',
        type: 'news',
        sourceType: 'online_news',
        title: 'Consumer spending defies gloom as retail sales beat expectations',
        snippet:
            'US retail sales rose 0.6% last month, well above forecasts. Resilient household demand, supported by real wage gains, continues to be the economy’s strongest buffer against a contraction.',
        url: 'https://www.ft.com/content/2026-04-28-us-retail-sales-beat-consumer-resilience',
        source: 'Financial Times',
        sourceDomain: 'ft.com',
        relevanceScore: 0.81,
        credibilityTier: 'tier_1',
        recencyWeight: 0.86,
        impactOnForecast: 'negative',
        impactMagnitude: 0.7,
        usedInAnswer: true,
        justification: 'Strong consumer spending is the main factor working against a recession call.',
        isKeyEvidence: false,
        rank: 4,
        publishedDaysAgo: 6,
    },
    {
        id: 'seed-ev-3',
        type: 'news',
        sourceType: 'online_news',
        title: 'Manufacturing activity contracts for fifth straight month',
        snippet:
            'The ISM manufacturing index held below 50 again, signalling sustained contraction. New orders fell sharply, pointing to weak industrial demand heading into the second half of 2026.',
        url: 'https://www.reuters.com/markets/us/ism-manufacturing-contracts-2026-05-03',
        source: 'Reuters',
        sourceDomain: 'reuters.com',
        relevanceScore: 0.78,
        credibilityTier: 'tier_1',
        recencyWeight: 0.9,
        impactOnForecast: 'positive',
        impactMagnitude: 0.62,
        usedInAnswer: true,
        justification: 'A prolonged manufacturing contraction adds weight to the downturn thesis.',
        isKeyEvidence: false,
        rank: 5,
        publishedDaysAgo: 9,
    },
    {
        id: 'seed-ev-4',
        type: 'news',
        sourceType: 'online_news',
        title: 'Inflation eases to 2.4% as price pressures continue to moderate',
        snippet:
            'Headline CPI fell to 2.4% year over year, the lowest reading since 2021. Cooling inflation gives policymakers more room to respond, though it leaves the growth outlook ambiguous.',
        url: 'https://www.wsj.com/economy/inflation-cpi-april-2026-eases',
        source: 'The Wall Street Journal',
        sourceDomain: 'wsj.com',
        relevanceScore: 0.58,
        credibilityTier: 'tier_2',
        recencyWeight: 0.72,
        impactOnForecast: 'neutral',
        impactMagnitude: 0.4,
        usedInAnswer: true,
        justification: 'Easing inflation is ambiguous for recession odds — it neither clearly raises nor lowers them.',
        isKeyEvidence: false,
        rank: 8,
        publishedDaysAgo: 13,
    },
    {
        id: 'seed-ev-5',
        type: 'expert',
        sourceType: 'fred',
        title: 'FRED: 10-year minus 3-month Treasury spread remains inverted',
        snippet:
            'The 10-year/3-month yield curve has been inverted for 14 consecutive months. Every US recession since 1970 has been preceded by a sustained inversion of this spread.',
        url: 'https://fred.stlouisfed.org/series/T10Y3M',
        source: 'Federal Reserve Economic Data',
        sourceDomain: 'fred.stlouisfed.org',
        relevanceScore: 0.95,
        credibilityTier: 'tier_1',
        recencyWeight: 0.8,
        impactOnForecast: 'positive',
        impactMagnitude: 0.9,
        usedInAnswer: true,
        justification: 'The yield-curve inversion is the single strongest historical recession predictor.',
        isKeyEvidence: true,
        rank: 2,
        publishedDaysAgo: 16,
    },
    {
        id: 'seed-ev-6',
        type: 'expert',
        sourceType: 'vault_arxiv',
        title: 'Nowcasting recession probability with mixed-frequency macro data',
        snippet:
            'This paper presents a dynamic factor model that estimates near-term recession probability. The authors caution that single-indicator signals frequently produce false positives in modern cycles.',
        url: 'https://arxiv.org/abs/2604.01827',
        source: 'arXiv',
        sourceDomain: 'arxiv.org',
        relevanceScore: 0.64,
        credibilityTier: 'tier_2',
        recencyWeight: 0.55,
        impactOnForecast: 'neutral',
        impactMagnitude: 0.45,
        usedInAnswer: false,
        justification: 'Methodological context; not a direct signal on the 2026 outcome.',
        isKeyEvidence: false,
        rank: 9,
        publishedDaysAgo: 19,
    },
    {
        id: 'seed-ev-7',
        type: 'expert',
        sourceType: 'vault_arxiv',
        title: 'Leading indicators and the timing of US business-cycle peaks',
        snippet:
            'An empirical review finding that the Conference Board’s leading index reliably turns negative six to nine months before a business-cycle peak. The index has been negative since early 2026.',
        url: 'https://arxiv.org/abs/2603.10094',
        source: 'arXiv',
        sourceDomain: 'arxiv.org',
        relevanceScore: 0.71,
        credibilityTier: 'tier_2',
        recencyWeight: 0.5,
        impactOnForecast: 'positive',
        impactMagnitude: 0.6,
        usedInAnswer: true,
        justification: 'A negative leading index supports the timing of a 2026 downturn.',
        isKeyEvidence: false,
        rank: 6,
        publishedDaysAgo: 22,
    },
    {
        id: 'seed-ev-8',
        type: 'social',
        sourceType: 'vault_hackernews',
        title: 'Discussion: is the soft-landing narrative finally breaking down?',
        snippet:
            'A widely-shared thread debates whether labor-market cracks invalidate the soft-landing thesis. Several commenters with finance backgrounds argue the downturn is already underway in cyclical sectors.',
        url: 'https://news.ycombinator.com/item?id=43219876',
        source: 'Hacker News',
        sourceDomain: 'news.ycombinator.com',
        relevanceScore: 0.49,
        credibilityTier: 'tier_3',
        recencyWeight: 0.65,
        impactOnForecast: 'negative',
        impactMagnitude: 0.35,
        usedInAnswer: true,
        justification: 'Informal sentiment signal; weak but leans against an imminent recession consensus.',
        isKeyEvidence: false,
        rank: 7,
        publishedDaysAgo: 4,
    },
    {
        id: 'seed-ev-9',
        type: 'social',
        sourceType: 'vault_hackernews',
        title: 'Thread: small-business owners on hiring plans for late 2026',
        snippet:
            'Anecdotes from small-business owners are mixed — some are freezing headcount while others report steady demand. No clear directional signal emerges from the discussion.',
        url: 'https://news.ycombinator.com/item?id=43287104',
        source: 'Hacker News',
        sourceDomain: 'news.ycombinator.com',
        relevanceScore: 0.46,
        credibilityTier: 'tier_3',
        recencyWeight: 0.3,
        impactOnForecast: 'neutral',
        impactMagnitude: 0.3,
        usedInAnswer: false,
        justification: 'Anecdotal and directionally mixed; included for completeness only.',
        isKeyEvidence: false,
        rank: 10,
        publishedDaysAgo: 11,
    },
    {
        id: 'seed-ev-10',
        type: 'market',
        sourceType: 'vault_market',
        title: 'Polymarket: "US recession in 2026" trading at 58 cents',
        snippet:
            'The Polymarket contract resolving YES if the NBER declares a 2026 recession is priced at 0.58, up from 0.51 a month ago. Volume has risen sharply alongside weak labor data.',
        url: 'https://polymarket.com/event/us-recession-in-2026',
        source: 'Polymarket',
        sourceDomain: 'polymarket.com',
        relevanceScore: 0.88,
        credibilityTier: 'tier_2',
        recencyWeight: 1.0,
        impactOnForecast: 'negative',
        impactMagnitude: 0.5,
        usedInAnswer: true,
        justification: 'Market consensus sits slightly below the model estimate, a mild moderating signal.',
        isKeyEvidence: false,
        rank: 3,
        publishedDaysAgo: 1,
    },
];

// ── Key factors ──────────────────────────────────────────────────
const KEY_FACTORS = [
    {
        label: 'Sustained yield-curve inversion',
        description:
            'The 10y/3m Treasury spread has been inverted for 14 months — a signal that has preceded every US recession since 1970.',
        direction: 'increases' as const,
        weight: 0.85,
        evidence_ids: ['seed-ev-5'],
    },
    {
        label: 'Softening labor market',
        description:
            'Jobless claims have risen for four straight weeks and the leading index has turned negative, pointing to a weakening cycle.',
        direction: 'increases' as const,
        weight: 0.7,
        evidence_ids: ['seed-ev-1', 'seed-ev-7'],
    },
    {
        label: 'Manufacturing contraction',
        description:
            'The ISM manufacturing index has stayed below 50 for five consecutive months, with new orders falling sharply.',
        direction: 'increases' as const,
        weight: 0.55,
        evidence_ids: ['seed-ev-3'],
    },
    {
        label: 'Resilient consumer spending',
        description:
            'Retail sales continue to beat expectations, supported by real wage gains — the economy’s strongest buffer against contraction.',
        direction: 'decreases' as const,
        weight: 0.65,
        evidence_ids: ['seed-ev-2'],
    },
    {
        label: 'Easing inflation pressure',
        description:
            'Headline CPI has fallen to 2.4%, reducing the risk that the Fed must keep policy restrictive into a slowdown.',
        direction: 'decreases' as const,
        weight: 0.5,
        evidence_ids: ['seed-ev-4'],
    },
    {
        label: 'Fed policy flexibility',
        description:
            'With inflation near target, the Fed retains room to cut rates pre-emptively and cushion the cycle.',
        direction: 'decreases' as const,
        weight: 0.4,
        evidence_ids: ['seed-ev-4', 'seed-ev-10'],
    },
];

// ── Reasoning chain ──────────────────────────────────────────────
const REASONING_CHAIN = [
    {
        step: 1,
        title: 'Frame the question',
        description:
            'Defined "recession" as an NBER-declared contraction beginning in calendar 2026, consistent with how prediction markets resolve this category.',
    },
    {
        step: 2,
        title: 'Weigh the leading indicators',
        description:
            'The yield-curve inversion and a negative leading index are the strongest historical predictors and both currently point toward a downturn.',
    },
    {
        step: 3,
        title: 'Assess the labor market',
        description:
            'Rising jobless claims and a five-month manufacturing contraction confirm that cyclical weakness is broadening beyond a single sector.',
    },
    {
        step: 4,
        title: 'Account for the offsetting buffer',
        description:
            'Resilient consumer spending and easing inflation materially soften the call — they are why this is a lean, not a confident, YES.',
    },
    {
        step: 5,
        title: 'Reconcile with the market',
        description:
            'The model’s 62% sits modestly above Polymarket’s 58%, reflecting slightly more weight on the inversion signal than the crowd.',
    },
];

// ── What the agent did not find ──────────────────────────────────
const WHAT_I_DIDNT_FIND = [
    'Q4 2025 PCE and personal-income revisions, which would sharpen the consumer-spending trajectory.',
    'Updated Fed dot-plot projections from the most recent FOMC meeting.',
    'Granular credit-spread data for high-yield corporate debt, a useful confirmation signal.',
];

// ── Summary markdown (must show bold + a bullet list) ────────────
const SUMMARY_MARKDOWN = [
    '## Forecast summary',
    '',
    'The evidence **leans toward a US recession in 2026**, but the call is deliberately held short of high conviction.',
    '',
    'The strongest signals pushing the probability up:',
    '',
    '- A **14-month yield-curve inversion** — historically the most reliable recession predictor.',
    '- A softening labor market, with jobless claims rising for four consecutive weeks.',
    '- A manufacturing sector in its fifth straight month of contraction.',
    '',
    'Working against the call is **resilient consumer spending**, which continues to beat expectations and remains the economy’s primary buffer. Easing inflation also gives the Fed room to act pre-emptively.',
    '',
    'On balance, the model settles at **62%** with **moderate consensus** across sources.',
].join('\n');

// ── Sentiment time series (10 points across ~30 days) ────────────
const PUBLIC_SENTIMENT = [0.34, 0.41, 0.36, 0.48, 0.44, 0.55, 0.5, 0.62, 0.57, 0.64];

// ── Agent events (8-step pipeline trace) ─────────────────────────
const AGENT_EVENTS = [
    { type: 'claim', title: 'Claimed query', description: 'Worker claimed the pending forecast query.', durationMs: 12 },
    { type: 'understand', title: 'Parsed question intent', description: 'Identified a binary macroeconomic forecast resolving in 2026.', durationMs: 340 },
    { type: 'embed', title: 'Generated query embedding', description: 'Built a text-embedding-3-small vector for vault retrieval.', durationMs: 280 },
    { type: 'vault_query', title: 'Searched evidence vault', description: 'Retrieved candidate evidence from the pgvector knowledge vault.', durationMs: 1200 },
    { type: 'rate_evidence', title: 'Rated 47 evidence items', description: 'Scored relevance, credibility and recency for all retrieved items.', durationMs: 8400 },
    { type: 'synthesize', title: 'Generated forecast (GPT-4o)', description: 'Synthesized probability, drivers, headwinds and reasoning chain.', durationMs: 12400 },
    { type: 'write', title: 'Wrote results to Firestore', description: 'Persisted the session result and evidence subcollection.', durationMs: 180 },
    { type: 'complete', title: 'Forecast complete', description: 'Flipped session status to done.', durationMs: 5 },
];

async function seed() {
    console.log('🌱 Seeding forecast session for UI development...\n');

    // 1. Session document — latestProbability/latestConfidence left null on
    //    purpose: Ron's pipeline does not write them, and Express derives
    //    latestProbability from sessionResults.finalProbability (Slice 9).
    const sessionRef = db.collection('sessions').doc(SESSION_ID);
    await sessionRef.set({
        userId: ADMIN_USER_ID,
        question: QUESTION,
        title: TITLE,
        status: 'done',
        latestProbability: null,
        latestConfidence: null,
        followEnabled: false,
        isFollowing: false,
        canonicalKey: null,
        errorCode: null,
        errorMessage: null,
        clarificationCandidates: null,
        createdAt,
        updatedAt,
        lastActivityAt: updatedAt,
    });
    console.log(`✅ sessions/${SESSION_ID}`);

    // 2. Session result.
    const resultRef = db.collection('sessionResults').doc(SESSION_ID);
    await resultRef.set({
        sessionId: SESSION_ID,
        userId: ADMIN_USER_ID,
        finalProbability: 0.62,
        confidence: 0.72,
        confidenceLabel: 'High Confidence',
        consensusStrength: 'Moderate',
        evidenceVolumeLabel: 'Medium',
        bottomLineAnswer:
            'The balance of evidence leans toward a US recession in 2026, driven by a sustained yield-curve inversion and a softening labor market — though resilient consumer spending keeps this short of a confident call.',
        detailedExplanation:
            'A 14-month inversion of the 10y/3m Treasury spread, rising jobless claims and a five-month manufacturing contraction together point to a meaningful and broadening cyclical slowdown. The clearest counterweight is consumer spending, which keeps beating expectations on the back of real wage gains, while easing inflation gives the Federal Reserve room to cut rates pre-emptively. Those offsets are why the forecast lands at a lean rather than a confident YES. The model’s 62% sits modestly above the Polymarket consensus of 58%.',
        summaryMarkdown: SUMMARY_MARKDOWN,
        marketComparison: [{ source: 'Polymarket', value: 0.58 }],
        marketProbability: 0.58,
        marketComparisonInsight:
            'Anizai’s 62% sits 4 points above the Polymarket consensus, reflecting slightly more weight on the yield-curve signal.',
        sentimentAnalysisInsight:
            'Expert sentiment has turned steadily more bearish over the past month, while public sentiment remains more volatile and lags the experts.',
        evidenceFeedSummary:
            'Ten sources spanning macro data, financial news and prediction markets, with the strongest weight on leading indicators.',
        keyFactors: KEY_FACTORS,
        whatIDidntFind: WHAT_I_DIDNT_FIND,
        reasoningChain: REASONING_CHAIN,
        suggestedActions: [],
        generatedAt,
        agentVersion: 'seed-script-v1',
        tier: 'tier_1',
        createdAt: generatedAt,
        updatedAt,
    });
    console.log(`✅ sessionResults/${SESSION_ID}`);

    // 3. Evidence subcollection.
    for (const ev of EVIDENCE) {
        const publishedAt = tsFromOffset(ev.publishedDaysAgo * DAY);
        await sessionRef.collection('evidence').doc(ev.id).set({
            type: ev.type,
            evidenceId: ev.id,
            sourceType: ev.sourceType,
            origin: 'vault',
            title: ev.title,
            snippet: ev.snippet,
            url: ev.url,
            source: ev.source,
            sourceDomain: ev.sourceDomain,
            sourceId: ev.id,
            publishedAt,
            fetchedAt: createdAt,
            score: ev.relevanceScore,
            relevanceScore: ev.relevanceScore,
            credibilityTier: ev.credibilityTier,
            recencyWeight: ev.recencyWeight,
            usedInAnswer: ev.usedInAnswer,
            impactOnForecast: ev.impactOnForecast,
            // impactMagnitude is written for forward-compatibility but is NOT
            // read by the server Evidence mapper today (see audit report).
            impactMagnitude: ev.impactMagnitude,
            justification: ev.justification,
            isKeyEvidence: ev.isKeyEvidence,
            rank: ev.rank,
            impact: null,
            impactLabel: null,
            createdAt,
        });
    }
    console.log(`✅ ${EVIDENCE.length} evidence docs`);

    // 4. Sentiment time series — 10 points, oldest first.
    for (let i = 0; i < 10; i++) {
        const daysAgo = 30 - i * 3;
        const ts = tsFromOffset(daysAgo * DAY);
        const expertSentiment = Number((0.45 + (i * 0.25) / 9).toFixed(3));
        await sessionRef.collection('sentimentTimeSeries').doc(`seed-st-${i + 1}`).set({
            ts,
            date: ts.toDate().toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
            expertSentiment,
            expertUpper: null,
            expertLower: null,
            publicSentiment: PUBLIC_SENTIMENT[i],
            createdAt,
        });
    }
    console.log('✅ 10 sentiment points');

    // 5. Agent events — 8-step pipeline trace across a ~22s execution window.
    let cursor = 0;
    for (let i = 0; i < AGENT_EVENTS.length; i++) {
        const ev = AGENT_EVENTS[i];
        const timestamp = Timestamp.fromMillis(createdAt.toMillis() + cursor);
        cursor += ev.durationMs;
        await sessionRef.collection('agentEvents').doc(`seed-ae-${i + 1}`).set({
            eventId: `seed-ae-${i + 1}`,
            sessionId: SESSION_ID,
            sequence: i + 1,
            timestamp,
            parentMessageId: null,
            type: ev.type,
            title: ev.title,
            description: ev.description,
            status: 'done',
            durationMs: ev.durationMs,
            payload: null,
        });
    }
    console.log(`✅ ${AGENT_EVENTS.length} agent events`);

    console.log('\n🎉 Seed complete.');
    console.log(`   sessionId: ${SESSION_ID}`);
    console.log(`   owner:     ${ADMIN_USER_ID}`);
}

seed()
    .then(() => process.exit(0))
    .catch((err) => {
        console.error('❌ Seed failed:', err);
        process.exit(1);
    });
