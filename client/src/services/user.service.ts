import { apiRequest } from '../lib/api';

export type UserPlan = 'free' | 'premium';

export interface UserProfile {
    uid: string;
    email: string;
    displayName: string | null;
    plan: UserPlan;
    planExpiresAt: string | null;
    cancelAtPeriodEnd: boolean;
    monthlyForecastsUsed: number;
    usageMonth: string;
    lastLoginAt: string;
    createdAt: string;
}

export function getDemoUserProfile(plan: UserPlan = 'free'): UserProfile {
    const now = new Date().toISOString();

    return {
        uid: 'demo-user-001',
        email: 'demo@anizai.local',
        displayName: 'Demo User',
        plan,
        planExpiresAt: null,
        cancelAtPeriodEnd: false,
        monthlyForecastsUsed: 1,
        usageMonth: now.slice(0, 7),
        lastLoginAt: now,
        createdAt: new Date('2026-01-01T00:00:00.000Z').toISOString(),
    };
}

export async function fetchCurrentUser(): Promise<UserProfile> {
    try {
        return await apiRequest<UserProfile>('/me');
    } catch (error) {
        console.warn('Using demo user because the authenticated API is unavailable.', error);
        return getDemoUserProfile();
    }
}

export async function updateUserPlan(
    plan: UserPlan
): Promise<UserProfile> {
    try {
        return await apiRequest<UserProfile>('/me/plan', {
            method: 'PATCH',
            body: { plan },
        });
    } catch (error) {
        console.warn('Using demo plan update because the authenticated API is unavailable.', error);
        return getDemoUserProfile(plan);
    }
}
