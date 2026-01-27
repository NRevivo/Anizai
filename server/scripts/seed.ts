/**
 * Seed script to insert demo data into Firestore
 * Run with: npx tsx scripts/seed.ts
 */

import { initializeApp, applicationDefault } from 'firebase-admin/app';
import { getFirestore, Timestamp } from 'firebase-admin/firestore';

// Initialize Firebase Admin with ADC
initializeApp({
    credential: applicationDefault(),
    projectId: process.env.FIREBASE_PROJECT_ID || 'anizai-ai',
});

const db = getFirestore();

// Demo user ID (you can replace this with a real Firebase Auth UID)
const DEMO_USER_ID = 'demo-user-001';

async function seed() {
    console.log('🌱 Seeding Firestore with demo data...\n');

    // 1. Create demo user
    const userRef = db.collection('users').doc(DEMO_USER_ID);
    const userData = {
        displayName: 'Demo User',
        email: 'demo@anizai.com',
        plan: 'free',
        planExpiresAt: null,
        usageMonth: '2026-01',
        monthlyForecastsUsed: 1,
        lastLoginAt: Timestamp.now(),
        createdAt: Timestamp.now(),
        updatedAt: Timestamp.now(),
    };
    await userRef.set(userData);
    console.log('✅ Created user:', DEMO_USER_ID);

    // 2. Create demo sessions
    const sessions = [
        {
            id: 'session-001',
            question: 'Will Bitcoin reach $100,000 by end of 2026?',
            title: 'Bitcoin $100K Prediction',
            status: 'done',
            latestProbability: 0.72,
            latestConfidence: 0.85,
        },
        {
            id: 'session-002',
            question: 'Will the Fed cut interest rates in Q1 2026?',
            title: 'Fed Rate Cut Q1',
            status: 'done',
            latestProbability: 0.45,
            latestConfidence: 0.78,
        },
        {
            id: 'session-003',
            question: 'Will AI regulation pass in the EU by 2027?',
            title: 'EU AI Regulation',
            status: 'running',
            latestProbability: 0.68,
            latestConfidence: 0.62,
        },
    ];

    for (const session of sessions) {
        const sessionRef = db.collection('sessions').doc(session.id);
        await sessionRef.set({
            userId: DEMO_USER_ID,
            question: session.question,
            title: session.title,
            status: session.status,
            latestProbability: session.latestProbability,
            latestConfidence: session.latestConfidence,
            followEnabled: false,
            isFollowing: false,
            canonicalKey: null,
            errorCode: null,
            errorMessage: null,
            createdAt: Timestamp.now(),
            updatedAt: Timestamp.now(),
            lastActivityAt: Timestamp.now(),
        });
        console.log(`✅ Created session: ${session.id} - "${session.title}"`);

        // Add some messages to each session
        const messagesRef = sessionRef.collection('messages');
        await messagesRef.add({
            role: 'user',
            content: session.question,
            createdAt: Timestamp.now(),
            status: 'sent',
            meta: null,
        });
        await messagesRef.add({
            role: 'assistant',
            content: `I'll analyze the factors affecting this prediction. Based on current data, the probability is ${Math.round(session.latestProbability * 100)}%.`,
            createdAt: Timestamp.now(),
            status: 'sent',
            meta: { model: 'gpt-4', tokensIn: 150, tokensOut: 200 },
        });
        console.log(`   📝 Added messages to ${session.id}`);

        // Add prediction series
        const seriesRef = sessionRef.collection('predictionSeries');
        const baseProb = session.latestProbability - 0.15;
        for (let i = 0; i < 5; i++) {
            await seriesRef.add({
                ts: Timestamp.fromDate(new Date(Date.now() - (4 - i) * 24 * 60 * 60 * 1000)),
                probability: Math.min(1, baseProb + i * 0.04),
                confidence: session.latestConfidence - 0.1 + i * 0.03,
                reasonType: i % 2 === 0 ? 'news' : 'market',
                evidenceIds: [],
            });
        }
        console.log(`   📈 Added prediction series to ${session.id}`);

        // Add evidence
        const evidenceRef = sessionRef.collection('evidence');
        await evidenceRef.add({
            type: 'news',
            title: 'Market Analysis Report',
            snippet: 'Recent trends indicate significant movement in this sector...',
            url: 'https://example.com/news/1',
            publishedAt: Timestamp.now(),
            sourceId: null,
            score: 0.85,
            createdAt: Timestamp.now(),
        });
        await evidenceRef.add({
            type: 'expert',
            title: 'Expert Opinion',
            snippet: 'Leading analysts suggest the probability is higher than initially expected.',
            url: null,
            publishedAt: Timestamp.now(),
            sourceId: null,
            score: 0.72,
            createdAt: Timestamp.now(),
        });
        console.log(`   🔍 Added evidence to ${session.id}`);
    }

    // 3. Create session results for completed sessions
    for (const session of sessions.filter((s) => s.status === 'done')) {
        const resultRef = db.collection('sessionResults').doc(session.id);
        await resultRef.set({
            sessionId: session.id,
            userId: DEMO_USER_ID,
            finalProbability: session.latestProbability,
            confidence: session.latestConfidence,
            marketComparison: [
                { source: 'Polymarket', value: session.latestProbability + 0.05 },
                { source: 'Metaculus', value: session.latestProbability - 0.08 },
            ],
            sentiment: [
                {
                    t: Timestamp.fromDate(new Date(Date.now() - 2 * 24 * 60 * 60 * 1000)),
                    expert: 0.6,
                    public: 0.7,
                },
                { t: Timestamp.now(), expert: 0.65, public: 0.75 },
            ],
            summaryMarkdown: `## Summary\n\nBased on analysis of ${session.question.toLowerCase()}, the current probability is **${Math.round(session.latestProbability * 100)}%** with ${Math.round(session.latestConfidence * 100)}% confidence.\n\n### Key Factors\n- Market sentiment is positive\n- Expert consensus aligns with prediction\n- Historical data supports this trajectory`,
            createdAt: Timestamp.now(),
            updatedAt: Timestamp.now(),
        });
        console.log(`✅ Created session result for: ${session.id}`);
    }

    // 4. Create trending forecasts
    const trendingRef = db.collection('trendingForecasts');
    const trending = [
        { question: 'Will GPT-5 be released in 2026?', tags: ['AI', 'OpenAI'], popularityScore: 95 },
        { question: 'Will Tesla stock reach $500 by year end?', tags: ['Stocks', 'Tesla'], popularityScore: 88 },
        { question: 'Will there be a major earthquake in California in 2026?', tags: ['Natural Events'], popularityScore: 72 },
    ];

    for (const t of trending) {
        await trendingRef.add({
            question: t.question,
            tags: t.tags,
            popularityScore: t.popularityScore,
            createdAt: Timestamp.now(),
            updatedAt: Timestamp.now(),
        });
    }
    console.log('✅ Created trending forecasts');

    console.log('\n🎉 Seed complete!');
    console.log('\nDemo user ID:', DEMO_USER_ID);
    console.log('Sessions created:', sessions.length);
}

seed()
    .then(() => process.exit(0))
    .catch((err) => {
        console.error('❌ Seed failed:', err);
        process.exit(1);
    });
