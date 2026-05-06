/**
 * Read-only probe — counts demo-user-001 ownership across collections.
 * Run: npx tsx scripts/probe-demo-footprint.ts
 */
import { firestore } from '../src/lib/firebase.js';

const DEMO_UID = 'demo-user-001';

type Row = { collection: string; field: string; count: number; sampleIds: string[] };

async function probe(): Promise<void> {
    const rows: Row[] = [];

    // Top-level collections that store userId
    for (const [name, field] of [
        ['sessions', 'userId'],
        ['sessionResults', 'userId'],
        ['forecastQueries', 'userId'],
    ] as const) {
        const snap = await firestore.collection(name).where(field, '==', DEMO_UID).get();
        rows.push({
            collection: name,
            field,
            count: snap.size,
            sampleIds: snap.docs.slice(0, 5).map((d) => d.id),
        });
    }

    // users/{uid} — doc id IS the uid (no field)
    const userDoc = await firestore.collection('users').doc(DEMO_UID).get();
    rows.push({
        collection: 'users',
        field: '<doc id>',
        count: userDoc.exists ? 1 : 0,
        sampleIds: userDoc.exists ? [DEMO_UID] : [],
    });

    // Subcollection messages — userId field per doc, but only on demo-owned sessions
    const sessionSnap = await firestore.collection('sessions').where('userId', '==', DEMO_UID).get();
    let messagesWithUserId = 0;
    let messagesWithoutUserId = 0;
    const sampleMessageIds: string[] = [];
    let evidenceTotal = 0;
    let predictionSeriesTotal = 0;
    let sentimentTotal = 0;
    let agentEventsTotal = 0;

    for (const sess of sessionSnap.docs) {
        const msgs = await sess.ref.collection('messages').get();
        for (const m of msgs.docs) {
            const uid = (m.data() as { userId?: string | null }).userId ?? null;
            if (uid === DEMO_UID) {
                messagesWithUserId++;
                if (sampleMessageIds.length < 5) {
                    sampleMessageIds.push(`${sess.id}/messages/${m.id}`);
                }
            } else if (uid === null || uid === undefined) {
                messagesWithoutUserId++;
            }
        }
        evidenceTotal += (await sess.ref.collection('evidence').get()).size;
        predictionSeriesTotal += (await sess.ref.collection('predictionSeries').get()).size;
        sentimentTotal += (await sess.ref.collection('sentimentTimeSeries').get()).size;
        agentEventsTotal += (await sess.ref.collection('agentEvents').get()).size;
    }

    rows.push({
        collection: 'sessions/*/messages',
        field: 'userId',
        count: messagesWithUserId,
        sampleIds: sampleMessageIds,
    });
    rows.push({
        collection: 'sessions/*/messages',
        field: '(no userId field)',
        count: messagesWithoutUserId,
        sampleIds: [],
    });
    rows.push({
        collection: 'sessions/*/evidence',
        field: '(no ownership field — implicit via parent)',
        count: evidenceTotal,
        sampleIds: [],
    });
    rows.push({
        collection: 'sessions/*/predictionSeries',
        field: '(no ownership field — implicit via parent)',
        count: predictionSeriesTotal,
        sampleIds: [],
    });
    rows.push({
        collection: 'sessions/*/sentimentTimeSeries',
        field: '(no ownership field — implicit via parent)',
        count: sentimentTotal,
        sampleIds: [],
    });
    rows.push({
        collection: 'sessions/*/agentEvents',
        field: '(no ownership field — implicit via parent)',
        count: agentEventsTotal,
        sampleIds: [],
    });

    console.table(rows.map((r) => ({
        Collection: r.collection,
        'Owner field': r.field,
        Matches: r.count,
        'Sample doc ids': r.sampleIds.join(', ') || '—',
    })));
}

probe().then(() => process.exit(0)).catch((err) => {
    console.error(err);
    process.exit(1);
});
