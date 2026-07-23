import { Timestamp } from 'firebase-admin/firestore';
import { firestore } from '../lib/firebase.js';

export const collections = {
    users: 'users',
    sessions: 'sessions',
    sessionResults: 'sessionResults',
    // `trendingForecasts` was removed here on 2026-07-20: /trending is a live
    // Polymarket passthrough and nothing reads the collection since the seeded
    // fallback was deleted (KG-C-11). The Firestore documents, the read rule in
    // firestore.rules and the writer in scripts/seed.ts still exist — removing the
    // rule needs a deploy, so it was left alone deliberately.
    forecastQueries: 'forecastQueries',
} as const;

export function collectionRef(name: keyof typeof collections) {
    return firestore.collection(collections[name]);
}

export function toISOString(timestamp: FirebaseFirestore.Timestamp | null | undefined): string | null {
    return timestamp?.toDate?.()?.toISOString() ?? null;
}

export function now(): FirebaseFirestore.Timestamp {
    return Timestamp.now();
}

export function batch() {
    return firestore.batch();
}

export function runTransaction<T>(
    updateFunction: (transaction: FirebaseFirestore.Transaction) => Promise<T>
): Promise<T> {
    return firestore.runTransaction(updateFunction);
}
