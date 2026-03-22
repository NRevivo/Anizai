import { Timestamp } from 'firebase-admin/firestore';
import { firestore } from '../lib/firebase.js';

export const collections = {
    users: 'users',
    sessions: 'sessions',
    sessionResults: 'sessionResults',
    trendingForecasts: 'trendingForecasts',
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
