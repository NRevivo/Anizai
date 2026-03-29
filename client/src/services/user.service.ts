import { apiRequest } from '../lib/api';

export type UserPlan = 'free' | 'premium';

export interface UserProfile {
    uid: string;
    email: string;
    displayName: string | null;
    plan: UserPlan;
    planExpiresAt: string | null;
    monthlyForecastsUsed: number;
    usageMonth: string;
    lastLoginAt: string;
    createdAt: string;
}

export async function fetchCurrentUser(): Promise<UserProfile> {
    return apiRequest<UserProfile>('/me');
}

export async function updateUserPlan(
    plan: UserPlan
): Promise<UserProfile> {
    return apiRequest<UserProfile>('/me/plan', {
        method: 'PATCH',
        body: { plan },
    });
}
