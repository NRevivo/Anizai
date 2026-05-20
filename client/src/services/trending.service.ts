import { apiRequest } from '../lib/api';
import { mockSessions } from '../data/mockData';

export interface TrendingForecast {
    id: string;
    question?: string;
    title?: string;
    popularityScore: number;
    probability?: number;
    createdAt?: string | null;
    updatedAt?: string | null;
}

export async function fetchTrendingForecasts(limit = 20): Promise<TrendingForecast[]> {
    try {
        const data = await apiRequest<TrendingForecast[]>(
            `/trending?limit=${encodeURIComponent(String(limit))}`,
            { requireAuth: false }
        );
        return data.slice(0, limit);
    } catch (error) {
        console.warn('Using demo trending forecasts because the API is unavailable.', error);
        return mockSessions.slice(0, limit).map((session) => ({
            id: session.id,
            question: session.question,
            popularityScore: Math.round((session.probability ?? 0.5) * 100),
            probability: session.probability ?? undefined,
            createdAt: session.lastUpdated.toISOString(),
            updatedAt: session.lastUpdated.toISOString(),
        }));
    }
}
