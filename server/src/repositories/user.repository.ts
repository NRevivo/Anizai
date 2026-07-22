import type { User } from '../services/users.service.js';
import { FieldValue } from 'firebase-admin/firestore';
import { collectionRef, now, runTransaction, toISOString } from '../services/firebase.service.js';
import { AppError } from '../middleware/error.js';
function getCurrentUsageMonth(date: Date): string {
    return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`;
}

function getUsageResetAt(date: Date): string {
    return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1, 0, 0, 0, 0)).toISOString();
}

const FREE_FORECAST_LIMIT = 3;

/** Whether a stored `planExpiresAt` (Timestamp or ISO string) is in the past. */
function isExpired(planExpiresAt: unknown): boolean {
    if (!planExpiresAt) {
        return false;
    }
    const expDate =
        typeof (planExpiresAt as { toDate?: () => Date }).toDate === 'function'
            ? (planExpiresAt as { toDate: () => Date }).toDate()
            : new Date(planExpiresAt as string);
    return Date.now() > expDate.getTime();
}

/** Resolve the canonical plan, tolerating legacy `membershipTier`/`subscriptionStatus`. */
function derivePlan(data: FirebaseFirestore.DocumentData): 'free' | 'premium' {
    return (data.plan ??
        data.membershipTier ??
        (data.subscriptionStatus === 'active' ? 'premium' : 'free')) as 'free' | 'premium';
}

/**
 * Shape a raw Firestore user document into the `User` type, normalizing legacy
 * fields and applying expiry-driven downgrade in memory. No writes — the read
 * path stays pure (KG-C-10c).
 */
function normalizeUser(id: string, data: FirebaseFirestore.DocumentData): User {
    const createdAt = toISOString(data.createdAt) ?? '';
    const updatedAt = toISOString(data.updatedAt) ?? '';
    const lastLoginAt = toISOString(data.lastLoginAt) ?? updatedAt ?? createdAt;
    const usageMonth =
        typeof data.usageMonth === 'string'
            ? data.usageMonth
            : getCurrentUsageMonth(new Date(createdAt || Date.now()));
    const plan = derivePlan(data);
    const monthlyForecastsUsed =
        typeof data.monthlyForecastsUsed === 'number' ? data.monthlyForecastsUsed : 0;
    const cancelAtPeriodEnd = data.cancelAtPeriodEnd ?? false;

    // Auto-downgrade a cancelled premium plan that has expired.
    const downgraded = plan === 'premium' && cancelAtPeriodEnd && isExpired(data.planExpiresAt);

    return {
        uid: id,
        email: data.email,
        displayName: data.displayName ?? data.fullName ?? null,
        admin: data.admin ?? false,
        plan: downgraded ? 'free' : plan,
        planExpiresAt:
            typeof data.planExpiresAt === 'string' ? data.planExpiresAt : toISOString(data.planExpiresAt),
        cancelAtPeriodEnd: downgraded ? false : cancelAtPeriodEnd,
        monthlyForecastsUsed,
        usageMonth,
        lastLoginAt,
        createdAt,
        updatedAt,
    };
}

export const userRepository = {
    /**
     * Get user by UID.
     *
     * Pure read — performs NO Firestore writes, so GET endpoints that read a
     * profile stay idempotent (KG-C-10c). Legacy documents are normalized in
     * memory here; the persistence of that normalization (deleting legacy
     * fields, backfilling canonical ones) rides the write paths — see
     * `reconcileProfile`, invoked from `syncFromAuth`. Expiry-driven downgrade
     * is likewise evaluated in memory for the returned snapshot and persisted
     * on the next write path (`incrementUsage`).
     */
    async findById(uid: string): Promise<User | null> {
        const userRef = collectionRef('users').doc(uid);
        const doc = await userRef.get();

        if (!doc.exists) {
            return null;
        }

        return normalizeUser(doc.id, doc.data()!);
    },

    /**
     * Persist the in-memory normalization `findById` computes: delete legacy
     * fields, backfill canonical ones, and flip an expired cancelled-premium
     * plan to free. A no-op write is skipped. Call only from write-intent
     * paths — never from a read (KG-C-10c).
     */
    async reconcileProfile(uid: string): Promise<void> {
        const userRef = collectionRef('users').doc(uid);
        const doc = await userRef.get();
        if (!doc.exists) {
            return;
        }

        const data = doc.data()!;
        const createdAt = toISOString(data.createdAt) ?? '';
        const usageMonth =
            typeof data.usageMonth === 'string'
                ? data.usageMonth
                : getCurrentUsageMonth(new Date(createdAt || Date.now()));
        const plan = derivePlan(data);
        const monthlyForecastsUsed =
            typeof data.monthlyForecastsUsed === 'number' ? data.monthlyForecastsUsed : 0;
        const cancelAtPeriodEnd = data.cancelAtPeriodEnd ?? false;

        const hasLegacyFields =
            data.fullName !== undefined ||
            data.subscriptionStatus !== undefined ||
            data.membershipTier !== undefined;
        const missingCanonicalFields =
            data.displayName === undefined ||
            data.plan === undefined ||
            data.planExpiresAt === undefined ||
            data.monthlyForecastsUsed === undefined ||
            data.usageMonth === undefined ||
            data.lastLoginAt === undefined;
        const expiredPremium =
            plan === 'premium' && cancelAtPeriodEnd && isExpired(data.planExpiresAt);

        if (!hasLegacyFields && !missingCanonicalFields && !expiredPremium) {
            return;
        }

        await userRef.set(
            {
                displayName: data.displayName ?? data.fullName ?? null,
                plan: expiredPremium ? 'free' : plan,
                planExpiresAt: data.planExpiresAt ?? null,
                cancelAtPeriodEnd: expiredPremium ? false : cancelAtPeriodEnd,
                monthlyForecastsUsed,
                usageMonth,
                lastLoginAt: data.lastLoginAt ?? data.updatedAt ?? data.createdAt ?? now(),
                updatedAt: now(),
                fullName: FieldValue.delete(),
                subscriptionStatus: FieldValue.delete(),
                membershipTier: FieldValue.delete(),
            },
            { merge: true }
        );
    },

    /**
     * Create a new user
     */
    async create(uid: string, email: string, displayName: string | null): Promise<User> {
        const createdAt = now();
        const createdDate = createdAt.toDate();
        const usageMonth = getCurrentUsageMonth(createdDate);
        const createdAtIso = createdDate.toISOString();

        const userData = {
            email,
            displayName,
            admin: false,
            plan: 'free' as const,
            planExpiresAt: null,
            cancelAtPeriodEnd: false,
            monthlyForecastsUsed: 0,
            usageMonth,
            lastLoginAt: createdAt,
            createdAt,
            updatedAt: createdAt,
        };

        await collectionRef('users').doc(uid).set(userData);

        return {
            uid,
            ...userData,
            lastLoginAt: createdAtIso,
            createdAt: createdAtIso,
            updatedAt: createdAtIso,
        };
    },

    /**
     * Update user fields sourced from Firebase Auth token.
     * Returns the latest stored user snapshot.
     */
    async syncFromAuth(uid: string, email: string, displayName: string | null): Promise<User | null> {
        const updatedAt = now();

        await collectionRef('users').doc(uid).set(
            {
                email,
                displayName,
                lastLoginAt: updatedAt,
                updatedAt,
            },
            { merge: true }
        );

        // Login is a genuine write path, so it is the natural moment to persist
        // the normalization `findById` no longer writes (KG-C-10c).
        await this.reconcileProfile(uid);

        return this.findById(uid);
    },

    async updatePlan(uid: string, plan: 'free' | 'premium'): Promise<User> {
        const updatedAt = now();
        const userRef = collectionRef('users').doc(uid);
        const doc = await userRef.get();
        const data = doc.data();

        if (plan === 'premium') {
            // Give 30 days of premium
            const expires = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000);
            await userRef.set(
                {
                    plan: 'premium',
                    planExpiresAt: expires.toISOString(),
                    cancelAtPeriodEnd: false,
                    updatedAt,
                },
                { merge: true }
            );
        } else {
            // User requested downgrade. If currently premium, set cancelAtPeriodEnd.
            const isCurrentlyPremium = data?.plan === 'premium' && (!data?.planExpiresAt || new Date(data.planExpiresAt) > new Date());
            
            if (isCurrentlyPremium) {
                const fallbackExpires = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();
                await userRef.set(
                    {
                        cancelAtPeriodEnd: true,
                        planExpiresAt: data.planExpiresAt || fallbackExpires,
                        updatedAt,
                    },
                    { merge: true }
                );
            } else {
                await userRef.set(
                    {
                        plan: 'free',
                        planExpiresAt: null,
                        cancelAtPeriodEnd: false,
                        updatedAt,
                    },
                    { merge: true }
                );
            }
        }

        const updated = await this.findById(uid);
        if (!updated) {
            throw new Error(`User not found after membership update: ${uid}`);
        }

        return updated;
    },

    /**
     * Check limits and increment usage for a given month automatically
     */
    async incrementUsage(uid: string): Promise<void> {
        const userRef = collectionRef('users').doc(uid);

        // The read, the limit check and the write must be one atomic unit.
        // As a plain read-then-write, two concurrent POST /sessions could both
        // read the same count and both write n+1, letting a free user exceed
        // FREE_FORECAST_LIMIT (KG-C-9). Firestore aborts and retries the whole
        // callback if the document changes underneath it.
        await runTransaction(async (transaction) => {
            const doc = await transaction.get(userRef);

            if (!doc.exists) {
                throw new AppError('User not found', 404, 'NOT_FOUND');
            }

            const data = doc.data()!;
            const currentMonth = getCurrentUsageMonth(new Date());

            let newUsage = typeof data.monthlyForecastsUsed === 'number' ? data.monthlyForecastsUsed : 0;

            // Lazy reset logic checking literal calendar month transition
            if (data.usageMonth !== currentMonth) {
                newUsage = 0;
            }

            // Evaluate the plan live: an expired cancelled-premium is treated as
            // free here, so enforcement no longer depends on a prior read having
            // persisted the downgrade (findById is now a pure read — KG-C-10c).
            const storedPlan = derivePlan(data);
            const downgraded =
                storedPlan === 'premium' && (data.cancelAtPeriodEnd ?? false) && isExpired(data.planExpiresAt);
            const plan = downgraded ? 'free' : storedPlan;

            // Limit completely blocks free tier execution
            if (plan === 'free' && newUsage >= FREE_FORECAST_LIMIT) {
                throw new AppError(
                    "You've used your free forecasts this month",
                    403,
                    'PLAN_LIMIT_EXCEEDED',
                    {
                        used: newUsage,
                        limit: FREE_FORECAST_LIMIT,
                        planTier: 'free',
                        resetAt: getUsageResetAt(new Date()),
                    }
                );
            }

            transaction.set(
                userRef,
                {
                    usageMonth: currentMonth,
                    monthlyForecastsUsed: newUsage + 1,
                    updatedAt: now(),
                    // Persist the downgrade we just evaluated, if any.
                    ...(downgraded ? { plan: 'free', cancelAtPeriodEnd: false } : {}),
                },
                { merge: true }
            );
        });
    }
};
