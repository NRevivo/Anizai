/**
 * Read-only probe — full inventory of the `sessions` collection.
 * Run: npx tsx scripts/probe-all-sessions.ts
 */
import { firestore } from '../src/lib/firebase.js';

const TARGET_ID = 'e2e-sprint20-fc6005a0';

function isTimestamp(value: unknown): value is FirebaseFirestore.Timestamp {
    return Boolean(
        value &&
        typeof value === 'object' &&
        'toDate' in value &&
        typeof (value as { toDate?: unknown }).toDate === 'function'
    );
}

function formatTimestamp(value: unknown): string {
    if (value === null || value === undefined) return '<missing>';
    if (isTimestamp(value)) return value.toDate().toISOString();
    if (typeof value === 'string') return value;
    return JSON.stringify(value);
}

function truncate(value: unknown, max = 80): string {
    if (value === null || value === undefined) return '';
    const str = typeof value === 'string' ? value : JSON.stringify(value);
    return str.length > max ? str.slice(0, max - 1) + '…' : str;
}

async function probe(): Promise<void> {
    const snap = await firestore.collection('sessions').get();
    const total = snap.size;

    type Row = { id: string; userId: string; status: string; createdAt: string; label: string };
    const rows: Row[] = [];
    const ownerCounts = new Map<string, number>();
    const missingOwner: string[] = [];

    for (const doc of snap.docs) {
        const data = doc.data() as Record<string, unknown>;
        const rawUid = data.userId;
        const uid = typeof rawUid === 'string' && rawUid.length > 0 ? rawUid : null;
        if (uid === null) {
            missingOwner.push(doc.id);
        }
        ownerCounts.set(uid ?? '<missing>', (ownerCounts.get(uid ?? '<missing>') ?? 0) + 1);

        const status = typeof data.status === 'string' ? data.status : '<missing>';
        const label = (data.title as string | undefined) ??
            (data.question as string | undefined) ??
            (data.query as string | undefined) ??
            '';

        rows.push({
            id: doc.id,
            userId: uid ?? '<missing>',
            status,
            createdAt: formatTimestamp(data.createdAt),
            label: truncate(label, 80),
        });
    }

    console.log(`\n=== Every document in \`sessions\` (total: ${total}) ===\n`);
    console.table(rows);

    console.log(`\n=== Sessions per userId (descending) ===\n`);
    const ownerBreakdown = Array.from(ownerCounts.entries())
        .sort((a, b) => b[1] - a[1])
        .map(([uid, count]) => ({ userId: uid, sessions: count }));
    console.table(ownerBreakdown);

    console.log(`\n=== Sessions with missing/null userId (count: ${missingOwner.length}) ===\n`);
    if (missingOwner.length === 0) {
        console.log('  (none)');
    } else {
        for (const id of missingOwner) console.log(`  - ${id}`);
    }

    console.log(`\n=== Targeted check: \`sessions/${TARGET_ID}\` ===\n`);
    const targetDoc = await firestore.collection('sessions').doc(TARGET_ID).get();
    if (!targetDoc.exists) {
        console.log(`  ✗ sessions/${TARGET_ID} does NOT exist.`);
    } else {
        console.log(`  ✓ sessions/${TARGET_ID} exists. Full document:\n`);
        console.dir(targetDoc.data(), { depth: 6 });

        console.log(`\n  Sub-collection counts:`);
        for (const sub of ['evidence', 'predictionSeries', 'sentimentTimeSeries', 'agentEvents', 'messages']) {
            const subSnap = await targetDoc.ref.collection(sub).get();
            console.log(`    - ${sub}: ${subSnap.size}`);
        }
    }

    console.log(`\n=== Targeted check: \`sessionResults/${TARGET_ID}\` ===\n`);
    const resultDoc = await firestore.collection('sessionResults').doc(TARGET_ID).get();
    if (!resultDoc.exists) {
        console.log(`  ✗ sessionResults/${TARGET_ID} does NOT exist.`);
    } else {
        console.log(`  ✓ sessionResults/${TARGET_ID} exists. Full document:\n`);
        console.dir(resultDoc.data(), { depth: 6 });
    }
}

probe()
    .then(() => process.exit(0))
    .catch((err) => {
        console.error(err);
        process.exit(1);
    });
