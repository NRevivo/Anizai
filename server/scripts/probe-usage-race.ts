/**
 * KG-C-9 concurrency probe — run against the Firestore emulator.
 *
 *   FIRESTORE_EMULATOR_HOST=localhost:8080 npx tsx scripts/probe-usage-race.ts
 *
 * Fires N simultaneous incrementUsage() calls at a fresh free-tier user and
 * reports how many were charged. With the transaction in place exactly
 * FREE_FORECAST_LIMIT should succeed and the rest must be rejected with
 * PLAN_LIMIT_EXCEEDED. Before the fix, concurrent callers observed the same
 * starting count and overshot the limit.
 */
import { userRepository } from '../src/repositories/user.repository.js';
import { collectionRef } from '../src/services/firebase.service.js';

const FREE_FORECAST_LIMIT = 3;
const CONCURRENCY = 10;
const UID = `race-probe-${Date.now()}`;

function currentMonth(d: Date): string {
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

async function main() {
    if (!process.env.FIRESTORE_EMULATOR_HOST) {
        console.error('Refusing to run: FIRESTORE_EMULATOR_HOST is not set.');
        console.error('This probe writes user documents and must never touch production.');
        process.exit(1);
    }

    const userRef = collectionRef('users').doc(UID);
    await userRef.set({
        email: `${UID}@example.test`,
        displayName: 'Race Probe',
        plan: 'free',
        monthlyForecastsUsed: 0,
        usageMonth: currentMonth(new Date()),
    });

    console.log(`Firing ${CONCURRENCY} simultaneous incrementUsage() calls`);
    console.log(`user=${UID}  plan=free  limit=${FREE_FORECAST_LIMIT}\n`);

    const results = await Promise.allSettled(
        Array.from({ length: CONCURRENCY }, () => userRepository.incrementUsage(UID))
    );

    const charged = results.filter((r) => r.status === 'fulfilled').length;
    const limited = results.filter(
        (r) => r.status === 'rejected' && r.reason?.code === 'PLAN_LIMIT_EXCEEDED'
    ).length;
    const other = results.filter(
        (r) => r.status === 'rejected' && r.reason?.code !== 'PLAN_LIMIT_EXCEEDED'
    );

    const stored = (await userRef.get()).data()?.monthlyForecastsUsed;

    console.log(`charged (fulfilled)      : ${charged}`);
    console.log(`refused PLAN_LIMIT       : ${limited}`);
    console.log(`other errors             : ${other.length}`);
    other.forEach((r) => r.status === 'rejected' && console.log(`   ! ${r.reason}`));
    console.log(`stored monthlyForecastsUsed : ${stored}`);

    const pass = charged === FREE_FORECAST_LIMIT && stored === FREE_FORECAST_LIMIT;
    console.log(
        `\n${pass ? 'PASS' : 'FAIL'} — expected exactly ${FREE_FORECAST_LIMIT} charged and stored, ` +
            `got ${charged} charged / ${stored} stored`
    );

    await userRef.delete();
    process.exit(pass ? 0 : 1);
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
