/**
 * Users Service
 * Minimal user profile management
 */

import { logger } from '../lib/logger.js';
import { userRepository } from '../repositories/user.repository.js';

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

export interface User {
    uid: string;
    email: string;
    displayName: string | null;
    admin: boolean;
    plan: 'free' | 'premium';
    planExpiresAt: string | null;
    monthlyForecastsUsed: number;
    usageMonth: string;
    lastLoginAt: string;
    createdAt: string;
    updatedAt: string;
}

export interface UserPublic {
    uid: string;
    email: string;
    displayName: string | null;
    plan: 'free' | 'premium';
    planExpiresAt: string | null;
    monthlyForecastsUsed: number;
    usageMonth: string;
    lastLoginAt: string;
    createdAt: string;
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function toPublicUser(user: User): UserPublic {
    return {
        uid: user.uid,
        email: user.email,
        displayName: user.displayName,
        plan: user.plan,
        planExpiresAt: user.planExpiresAt,
        monthlyForecastsUsed: user.monthlyForecastsUsed,
        usageMonth: user.usageMonth,
        lastLoginAt: user.lastLoginAt,
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
    return userRepository.findById(uid);
}

/**
 * Get or create user
 * Creates a new user document if one doesn't exist for the given UID
 */
export async function getOrCreateUser(uid: string, email: string, displayName: string | null): Promise<User> {
    const existingUser = await userRepository.findById(uid);

    if (existingUser) {
        const shouldSyncEmail = existingUser.email !== email;
        const shouldSyncName = existingUser.displayName !== displayName;

        if (shouldSyncEmail || shouldSyncName) {
            const synced = await userRepository.syncFromAuth(uid, email, displayName);
            if (synced) {
                return synced;
            }
        }

        return existingUser;
    }

    // Create new user
    const newUser = await userRepository.create(uid, email, displayName);

    logger.info({ uid, email }, 'Created new user');

    return newUser;
}

/**
 * Get public user profile (excludes sensitive fields like admin)
 */
export async function getPublicProfile(uid: string, email: string, displayName: string | null): Promise<UserPublic> {
    const user = await getOrCreateUser(uid, email, displayName);
    return toPublicUser(user);
}

/**
 * Update user's plan (free | premium)
 */
export async function updatePlan(
    uid: string,
    plan: 'free' | 'premium'
): Promise<UserPublic> {
    const updated = await userRepository.updatePlan(uid, plan);
    return toPublicUser(updated);
}

/**
 * Increment usage and check plan limits
 */
export async function incrementUsage(uid: string): Promise<void> {
    return userRepository.incrementUsage(uid);
}
