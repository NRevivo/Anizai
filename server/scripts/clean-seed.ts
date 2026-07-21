/**
 * Remove seeded forecast sessions written by seed-forecast.ts.
 *
 * Finds every sessions/{id} owned by the admin user whose title starts
 * with "[SEED]", and deletes it together with its subcollections and the
 * matching sessionResults/{id} document.
 *
 * Dry-run (default):  cd server && npm run seed:clean
 * Actually delete:    cd server && npm run seed:clean -- --yes
 */

import 'dotenv/config';
import { initializeApp, applicationDefault } from 'firebase-admin/app';
import { getFirestore } from 'firebase-admin/firestore';

initializeApp({
    credential: applicationDefault(),
    projectId: process.env.FIREBASE_PROJECT_ID || 'anizai-ai',
});

const db = getFirestore();

const ADMIN_USER_ID = 'gEnzUuBLpcNwITpow33AqEnfFCs1';
const SEED_TITLE_PREFIX = '[SEED]';
const SUBCOLLECTIONS = ['evidence', 'sentimentTimeSeries', 'agentEvents', 'messages', 'predictionSeries'];

const APPLY = process.argv.includes('--yes');

async function countSubcollectionDocs(sessionId: string): Promise<number> {
    let total = 0;
    for (const name of SUBCOLLECTIONS) {
        const snap = await db.collection('sessions').doc(sessionId).collection(name).get();
        total += snap.size;
    }
    return total;
}

async function deleteSubcollectionDocs(sessionId: string): Promise<number> {
    let deleted = 0;
    for (const name of SUBCOLLECTIONS) {
        const ref = db.collection('sessions').doc(sessionId).collection(name);
        // Per-session subcollections are small; one batch is sufficient.
        const snap = await ref.get();
        if (snap.empty) {
            continue;
        }
        const batch = db.batch();
        snap.docs.forEach((doc) => batch.delete(doc.ref));
        await batch.commit();
        deleted += snap.size;
    }
    return deleted;
}

async function cleanSeed() {
    console.log(`🧹 ${APPLY ? 'Deleting' : 'Dry-run:'} seeded sessions for ${ADMIN_USER_ID}\n`);

    const snapshot = await db.collection('sessions').where('userId', '==', ADMIN_USER_ID).get();
    const seeded = snapshot.docs.filter((doc) => {
        const title = doc.data().title;
        return typeof title === 'string' && title.startsWith(SEED_TITLE_PREFIX);
    });

    if (seeded.length === 0) {
        console.log('No seeded sessions found. Nothing to do.');
        return;
    }

    let totalSubDocs = 0;
    for (const doc of seeded) {
        const subCount = await countSubcollectionDocs(doc.id);
        totalSubDocs += subCount;
        console.log(`  • sessions/${doc.id}  "${doc.data().title}"  (${subCount} subcollection docs)`);

        if (APPLY) {
            const deletedSubs = await deleteSubcollectionDocs(doc.id);
            await db.collection('sessionResults').doc(doc.id).delete();
            await doc.ref.delete();
            console.log(`    ↳ deleted session, result, and ${deletedSubs} subcollection docs`);
        }
    }

    console.log('');
    if (APPLY) {
        console.log(`✅ Deleted ${seeded.length} session(s) and ${totalSubDocs} subcollection doc(s).`);
    } else {
        console.log(`Dry-run: would delete ${seeded.length} session(s) and ${totalSubDocs} subcollection doc(s).`);
        console.log('Re-run with `-- --yes` to apply.');
    }
}

cleanSeed()
    .then(() => process.exit(0))
    .catch((err) => {
        console.error('❌ Cleanup failed:', err);
        process.exit(1);
    });
