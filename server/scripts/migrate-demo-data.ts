/**
 * One-time migration: reassign every Firestore document owned by the
 * legacy `demo-user-001` UID to a real Firebase Auth user.
 *
 * Background:
 *   The original frontend short-circuited login as a fixed demo user
 *   (`demo-user-001`). Restoring real auth means real users own no
 *   sessions until this migration runs against the legacy seed data.
 *
 * Footprint (verified by scripts/probe-demo-footprint.ts on 2026-05-06):
 *   - sessions                    userId field
 *   - sessionResults              userId field
 *   - forecastQueries             userId field (defensive — none on disk today)
 *   - sessions/<id>/messages      userId field (defensive — none on disk today)
 *   Subcollections evidence, predictionSeries, sentimentTimeSeries,
 *   agentEvents have no ownership field — they inherit from the parent
 *   session and need no rewrite.
 *
 * Usage (dry-run by default):
 *   npm run migrate:demo-data -- --email=viewer@anizai.local --password=<pwd>
 *   npm run migrate:demo-data -- --target-uid=<existingUid>
 *   npm run migrate:demo-data -- --email=... --password=... --apply
 *   npm run migrate:demo-data -- --target-uid=... --demo-uid=other-uid --apply
 */

import { adminAuth, firestore } from '../src/lib/firebase.js';

interface Args {
    email?: string;
    password?: string;
    targetUid?: string;
    demoUid: string;
    apply: boolean;
}

function parseArgs(argv: string[]): Args {
    const out: Args = { demoUid: 'demo-user-001', apply: false };
    for (const arg of argv) {
        if (arg === '--apply') {
            out.apply = true;
            continue;
        }
        const m = arg.match(/^--([a-z-]+)=(.*)$/i);
        if (!m) continue;
        const key = m[1];
        const val = m[2];
        switch (key) {
            case 'email':
                out.email = val;
                break;
            case 'password':
                out.password = val;
                break;
            case 'target-uid':
                out.targetUid = val;
                break;
            case 'demo-uid':
                out.demoUid = val;
                break;
            default:
                console.warn(`Ignoring unknown flag: --${key}`);
        }
    }
    if (!out.targetUid && !(out.email && out.password)) {
        throw new Error(
            'Provide either --target-uid=<uid> OR both --email=<...> and --password=<...>'
        );
    }
    return out;
}

async function resolveTargetUid(args: Args): Promise<string> {
    if (args.targetUid) {
        // Confirm the user exists; fail fast if not.
        await adminAuth.getUser(args.targetUid);
        console.log(`✓ Target UID ${args.targetUid} verified in Firebase Auth.`);
        return args.targetUid;
    }
    // email + password path
    const email = args.email!;
    const password = args.password!;

    if (!args.apply) {
        console.log(`[dry-run] Would create Firebase Auth user with email=${email} (password redacted).`);
        return '<would-be-created-uid>';
    }

    // Idempotent on email: if a user already exists, reuse it instead of erroring.
    try {
        const existing = await adminAuth.getUserByEmail(email);
        console.log(`⚠ User with email ${email} already exists (uid=${existing.uid}). Reusing.`);
        return existing.uid;
    } catch (err) {
        const code = (err as { code?: string }).code;
        if (code !== 'auth/user-not-found') {
            throw err;
        }
    }

    const created = await adminAuth.createUser({
        email,
        password,
        emailVerified: true,
        displayName: 'Anizai Test Viewer',
    });
    console.log(`✓ Created Firebase Auth user uid=${created.uid} email=${email}`);
    return created.uid;
}

const FIRESTORE_BATCH_MAX_OPS = 500;

interface CollectionReport {
    collection: string;
    matched: number;
    wouldUpdate: number;
    skippedAlreadyMigrated: number;
    sampleIds: string[];
}

async function migrateTopLevel(
    collectionName: string,
    demoUid: string,
    targetUid: string,
    apply: boolean
): Promise<CollectionReport> {
    const snap = await firestore
        .collection(collectionName)
        .where('userId', '==', demoUid)
        .get();

    const report: CollectionReport = {
        collection: collectionName,
        matched: snap.size,
        wouldUpdate: 0,
        skippedAlreadyMigrated: 0,
        sampleIds: snap.docs.slice(0, 5).map((d) => d.id),
    };

    if (snap.empty) return report;

    let writeBatch = firestore.batch();
    let opsInBatch = 0;
    for (const doc of snap.docs) {
        const current = (doc.data() as { userId?: string | null }).userId;
        if (current === targetUid) {
            report.skippedAlreadyMigrated++;
            continue;
        }
        report.wouldUpdate++;
        if (apply) {
            writeBatch.update(doc.ref, { userId: targetUid });
            opsInBatch++;
            if (opsInBatch >= FIRESTORE_BATCH_MAX_OPS) {
                await writeBatch.commit();
                writeBatch = firestore.batch();
                opsInBatch = 0;
            }
        }
    }
    if (apply && opsInBatch > 0) {
        await writeBatch.commit();
    }
    return report;
}

async function migrateSubcollectionMessages(
    demoUid: string,
    targetUid: string,
    apply: boolean
): Promise<CollectionReport> {
    // Walk every session that ALREADY belongs to demoUid OR targetUid (to
    // catch sessions we updated in this run; a re-run after partial apply
    // shouldn't lose track of orphan messages).
    const sessionSnaps = await firestore
        .collection('sessions')
        .where('userId', 'in', [demoUid, targetUid])
        .get();

    const report: CollectionReport = {
        collection: 'sessions/*/messages',
        matched: 0,
        wouldUpdate: 0,
        skippedAlreadyMigrated: 0,
        sampleIds: [],
    };

    let writeBatch = firestore.batch();
    let opsInBatch = 0;
    for (const sess of sessionSnaps.docs) {
        const msgs = await sess.ref
            .collection('messages')
            .where('userId', '==', demoUid)
            .get();
        report.matched += msgs.size;
        for (const m of msgs.docs) {
            report.wouldUpdate++;
            if (report.sampleIds.length < 5) {
                report.sampleIds.push(`${sess.id}/messages/${m.id}`);
            }
            if (apply) {
                writeBatch.update(m.ref, { userId: targetUid });
                opsInBatch++;
                if (opsInBatch >= FIRESTORE_BATCH_MAX_OPS) {
                    await writeBatch.commit();
                    writeBatch = firestore.batch();
                    opsInBatch = 0;
                }
            }
        }
    }
    if (apply && opsInBatch > 0) {
        await writeBatch.commit();
    }
    return report;
}

function printReport(reports: CollectionReport[], targetUid: string, apply: boolean): void {
    const mode = apply ? 'APPLY' : 'DRY-RUN';
    console.log(`\n=== ${mode} summary (target uid: ${targetUid}) ===\n`);
    console.table(
        reports.map((r) => ({
            Collection: r.collection,
            Matched: r.matched,
            [apply ? 'Updated' : 'WouldUpdate']: r.wouldUpdate,
            'Skipped (already migrated)': r.skippedAlreadyMigrated,
            'Sample IDs': r.sampleIds.join(', ') || '—',
        }))
    );
    if (!apply) {
        console.log('\nDry-run complete. Re-run with --apply to commit changes.');
    } else {
        console.log('\n✓ Migration applied.');
    }
}

async function main(): Promise<void> {
    const args = parseArgs(process.argv.slice(2));
    console.log(`Mode: ${args.apply ? 'APPLY' : 'DRY-RUN'}`);
    console.log(`Demo UID (source): ${args.demoUid}`);

    const targetUid = await resolveTargetUid(args);
    console.log(`Target UID: ${targetUid}\n`);

    if (!args.apply && !args.targetUid) {
        // In dry-run with email/password we don't have a real UID — so
        // we can still query and count, but we can't compare against
        // "already migrated". Use a sentinel that won't match anything.
    }

    const effectiveTarget = targetUid === '<would-be-created-uid>'
        ? '__DRY_RUN_PLACEHOLDER__'
        : targetUid;

    const reports: CollectionReport[] = [];
    reports.push(await migrateTopLevel('sessions', args.demoUid, effectiveTarget, args.apply));
    reports.push(await migrateTopLevel('sessionResults', args.demoUid, effectiveTarget, args.apply));
    reports.push(await migrateTopLevel('forecastQueries', args.demoUid, effectiveTarget, args.apply));
    if (args.apply) {
        // For subcollection messages we need a real target UID so the
        // post-migration query can union demo+target sessions.
        reports.push(await migrateSubcollectionMessages(args.demoUid, effectiveTarget, args.apply));
    } else {
        // Dry-run: only walk demo-owned sessions.
        const sessionSnaps = await firestore
            .collection('sessions')
            .where('userId', '==', args.demoUid)
            .get();
        const subReport: CollectionReport = {
            collection: 'sessions/*/messages',
            matched: 0,
            wouldUpdate: 0,
            skippedAlreadyMigrated: 0,
            sampleIds: [],
        };
        for (const sess of sessionSnaps.docs) {
            const msgs = await sess.ref
                .collection('messages')
                .where('userId', '==', args.demoUid)
                .get();
            subReport.matched += msgs.size;
            subReport.wouldUpdate += msgs.size;
            for (const m of msgs.docs) {
                if (subReport.sampleIds.length < 5) {
                    subReport.sampleIds.push(`${sess.id}/messages/${m.id}`);
                }
            }
        }
        reports.push(subReport);
    }

    printReport(reports, targetUid, args.apply);

    console.log('\nNote: subcollections evidence / predictionSeries /');
    console.log('sentimentTimeSeries / agentEvents have no ownership field —');
    console.log('they inherit from the parent session and need no rewrite.');
    console.log('users/demo-user-001 is intentionally left in place.');
}

main()
    .then(() => process.exit(0))
    .catch((err) => {
        console.error('\n❌ Migration failed:', err);
        process.exit(1);
    });
