import { apiRequest } from '../lib/api';

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
    const data = await apiRequest<TrendingForecast[]>('/trending', { requireAuth: false });
    return data.slice(0, limit);
}
