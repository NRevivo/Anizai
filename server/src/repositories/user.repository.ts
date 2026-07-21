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

export const userRepository = {
    /**
     * Get user by UID
     */
    async findById(uid: string): Promise<User | null> {
        const userRef = collectionRef('users').doc(uid);
        const doc = await userRef.get();

        if (!doc.exists) {
            return null;
        }

        const data = doc.data()!;
        const createdAt = toISOString(data.createdAt) ?? '';
        const updatedAt = toISOString(data.updatedAt) ?? '';
        const lastLoginAt = toISOString(data.lastLoginAt) ?? updatedAt ?? createdAt;
        const usageMonth =
            typeof data.usageMonth === 'string'
                ? data.usageMonth
                : getCurrentUsageMonth(new Date(createdAt || Date.now()));
        const plan = (data.plan ??
            data.membershipTier ??
            (data.subscriptionStatus === 'active' ? 'premium' : 'free')) as 'free' | 'premium';
        const monthlyForecastsUsed =
            typeof data.monthlyForecastsUsed === 'number' ? data.monthlyForecastsUsed : 0;
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

        const cancelAtPeriodEnd = data.cancelAtPeriodEnd ?? false;

        if (hasLegacyFields || missingCanonicalFields) {
            await userRef.set(
                {
                    displayName: data.displayName ?? data.fullName ?? null,
                    plan,
                    planExpiresAt: data.planExpiresAt ?? null,
                    cancelAtPeriodEnd,
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
        }

        // Auto-downgrade logic if a cancelled premium plan has expired
        let currentPlan = plan;
        let currentCancelFlag = cancelAtPeriodEnd;
        if (currentPlan === 'premium' && currentCancelFlag && data.planExpiresAt) {
            const expDate = data.planExpiresAt.toDate ? data.planExpiresAt.toDate() : new Date(data.planExpiresAt);
            if (Date.now() > expDate.getTime()) {
                currentPlan = 'free';
                currentCancelFlag = false;
                await userRef.set({ plan: 'free', cancelAtPeriodEnd: false, updatedAt: now() }, { merge: true });
            }
        }

        return {
            uid: doc.id,
            email: data.email,
            displayName: data.displayName ?? data.fullName ?? null,
            admin: data.admin ?? false,
            plan: currentPlan,
            planExpiresAt: typeof data.planExpiresAt === 'string' ? data.planExpiresAt : toISOString(data.planExpiresAt),
            cancelAtPeriodEnd: currentCancelFlag,
            monthlyForecastsUsed,
            usageMonth,
            lastLoginAt,
            createdAt,
            updatedAt,
        };
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

            const plan = data.plan || 'free';

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
                },
                { merge: true }
            );
        });
    }
};
