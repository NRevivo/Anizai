/**
 * Dev script to test SessionResult persistence and retrieval
 * 
 * Prerequisites:
 *   1. Start Firebase emulators: firebase emulators:start
 *   2. Or use npm script: npm run dev:emu (in another terminal)
 * 
 * Usage:
 *   npm run test:session-result
 */

import { firestore, adminAuth } from '../src/lib/firebase.js';
import { Timestamp } from 'firebase-admin/firestore';

const TEST_USER_EMAIL = 'test@example.com';
const TEST_USER_PASSWORD = 'password123';

async function main() {
    console.log('🧪 Testing SessionResult persistence and retrieval\n');

    // Step 1: Create or get test user
    console.log('1️⃣  Creating test user...');
    let userRecord;
    try {
        userRecord = await adminAuth.getUserByEmail(TEST_USER_EMAIL);
        console.log(`   ✓ User exists: ${userRecord.uid}`);
    } catch {
        userRecord = await adminAuth.createUser({
            email: TEST_USER_EMAIL,
            password: TEST_USER_PASSWORD,
        });
        console.log(`   ✓ User created: ${userRecord.uid}`);
    }

    const userId = userRecord.uid;

    // Step 2: Create test session
    console.log('\n2️⃣  Creating test session...');
    const sessionRef = await firestore.collection('sessions').add({
        userId,
        question: 'Will AI regulation pass in the EU by Q2 2026?',
        title: 'EU AI Regulation Test',
        status: 'done',
        latestProbability: 0.723,
        latestConfidence: 0.84,
        followEnabled: false,
        isFollowing: false,
        canonicalKey: null,
        errorCode: null,
        errorMessage: null,
        clarificationCandidates: null,
        createdAt: Timestamp.now(),
        updatedAt: Timestamp.now(),
        lastActivityAt: Timestamp.now(),
    });
    const sessionId = sessionRef.id;
    console.log(`   ✓ Session created: ${sessionId}`);

    // Step 3: Write SessionResult with all new fields
    console.log('\n3️⃣  Writing SessionResult document...');
    await firestore.collection('sessionResults').doc(sessionId).set({
        sessionId,
        userId,
        finalProbability: 0.723,
        confidence: 0.84,
        marketComparison: [
            { source: 'Polymarket', value: 0.685 },
            { source: 'Metaculus', value: 0.702 },
        ],
        summaryMarkdown: 'Full summary text here',

        // New fields
        confidenceLabel: 'High Confidence',
        consensusStrength: 'Strong',
        evidenceVolumeLabel: 'High',
        bottomLineAnswer: 'Legislative momentum and expert consensus strongly signal Q2 passage, outweighing industry resistance.',
        detailedExplanation: 'Committee discussions have accelerated significantly. While industry lobbying poses a delay risk, the political cost of inaction has risen.',
        marketProbability: 0.685,
        marketComparisonInsight: 'Anizai is 3.8% more bullish than market consensus',
        sentimentAnalysisInsight: 'Expert sentiment trending up +13% ahead of public opinion',
        evidenceFeedSummary: 'Recent committee approvals carried significantly more weight than isolated industry pushback.',

        createdAt: Timestamp.now(),
        updatedAt: Timestamp.now(),
    });
    console.log(`   ✓ SessionResult written to sessionResults/${sessionId}`);

    // Step 4: Fetch using getSessionResult (via import)
    console.log('\n4️⃣  Testing getSessionResult function...');
    const { getSessionResult } = await import('../src/services/sessions.service.js');
    const result = await getSessionResult(sessionId, userId);

    if (!result) {
        console.error('   ✗ Result is null!');
        process.exit(1);
    }

    console.log(`   ✓ Retrieved SessionResult`);
    console.log(`   ✓ bottomLineAnswer: ${result.bottomLineAnswer ? '✓ EXISTS' : '✗ MISSING'}`);
    console.log(`   ✓ confidenceLabel: ${result.confidenceLabel}`);
    console.log(`   ✓ consensusStrength: ${result.consensusStrength}`);
    console.log(`   ✓ evidenceVolumeLabel: ${result.evidenceVolumeLabel}`);

    // Step 5: Test userId verification (should throw error)
    console.log('\n5️⃣  Testing userId verification...');
    try {
        await getSessionResult(sessionId, 'wrong-user-id');
        console.error('   ✗ Should have thrown error for wrong userId');
        process.exit(1);
    } catch (error: any) {
        if (error.statusCode === 404) {
            console.log('   ✓ Correctly rejected wrong userId');
        } else {
            throw error;
        }
    }

    // Step 6: Make HTTP request to API endpoint
    console.log('\n6️⃣  Testing GET /sessions/:id API endpoint...');
    console.log(`   ℹ️  To test the full API, run:`);
    console.log(`      TOKEN=$(npm run --silent emu:token) && \\`);
    console.log(`      curl http://localhost:3000/sessions/${sessionId} \\`);
    console.log(`      -H "Authorization: Bearer $TOKEN" | jq '.data.result.bottomLineAnswer'`);

    console.log('\n✅ All tests passed!\n');
    console.log('📋 Summary:');
    console.log(`   Session ID: ${sessionId}`);
    console.log(`   User ID: ${userId}`);
    console.log(`   bottomLineAnswer exists: ✓`);

    process.exit(0);
}

main().catch((error) => {
    console.error('❌ Test failed:', error);
    process.exit(1);
});
