/**
 * Users Service
 * Minimal user profile management
 */

import { firestore } from '../lib/firebase.js';
import { Timestamp } from 'firebase-admin/firestore';
import { logger } from '../lib/logger.js';

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

export interface User {
    uid: string;
    email: string;
    fullName: string | null;
    admin: boolean;
    subscriptionStatus: 'none' | 'active' | 'cancelled' | 'past_due';
    createdAt: string;
    updatedAt: string;
}

export interface UserPublic {
    uid: string;
    email: string;
    fullName: string | null;
    subscriptionStatus: string;
    createdAt: string;
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function toISOString(timestamp: FirebaseFirestore.Timestamp | null | undefined): string | null {
    return timestamp?.toDate?.()?.toISOString() ?? null;
}

function toPublicUser(user: User): UserPublic {
    return {
        uid: user.uid,
        email: user.email,
        fullName: user.fullName,
        subscriptionStatus: user.subscriptionStatus,
        createdAt: user.createdAt,
    };
}

// ─────────────────────────────────────────────────────────────
// Service Methods
// ─────────────────────────────────────────────────────────────

/**
 * Get user by UID
 */
export async function getUser(uid: string): Promise<User | null> {
    const doc = await firestore.collection('users').doc(uid).get();

    if (!doc.exists) {
        return null;
    }

    const data = doc.data()!;

    return {
        uid: doc.id,
        email: data.email,
        fullName: data.fullName ?? null,
        admin: data.admin ?? false,
        subscriptionStatus: data.subscriptionStatus ?? 'none',
        createdAt: toISOString(data.createdAt) ?? '',
        updatedAt: toISOString(data.updatedAt) ?? '',
    };
}

/**
 * Get or create user
 * Creates a new user document if one doesn't exist for the given UID
 */
export async function getOrCreateUser(uid: string, email: string): Promise<User> {
    const existingUser = await getUser(uid);

    if (existingUser) {
        return existingUser;
    }

    // Create new user
    const now = Timestamp.now();

    const userData = {
        email,
        fullName: null,
        admin: false,
        subscriptionStatus: 'none' as const,
        createdAt: now,
        updatedAt: now,
    };

    await firestore.collection('users').doc(uid).set(userData);

    logger.info({ uid, email }, 'Created new user');

    return {
        uid,
        ...userData,
        createdAt: now.toDate().toISOString(),
        updatedAt: now.toDate().toISOString(),
    };
}

/**
 * Get public user profile (excludes sensitive fields like admin)
 */
export async function getPublicProfile(uid: string, email: string): Promise<UserPublic> {
    const user = await getOrCreateUser(uid, email);
    return toPublicUser(user);
}
