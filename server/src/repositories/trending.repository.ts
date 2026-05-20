import { collectionRef, toISOString } from '../services/firebase.service.js';

export interface TrendingForecast {
    id: string;
    popularityScore: number;
    question?: string;
    probability?: number;
    tags?: string[];
    [key: string]: unknown;
}

// In-memory cache for the Polymarket fetch. The /trending endpoint is public
// (the landing page hits it unauthenticated), so without a cache every page
// view would fan out to gamma-api.polymarket.com. We keep a single shared
// payload at the largest limit anyone asks for, and slice client-side.
const TTL_MS = 5 * 60 * 1000;
let cache: { fetchedAt: number; limit: number; data: TrendingForecast[] } | null = null;
let inflight: Promise<TrendingForecast[]> | null = null;

export const trendingRepository = {
    /**
     * Get top trending forecasts by popularity score
     * Fetches real active markets from Polymarket Gamma API, falls back to Firestore mock data
     */
    async getTopTrending(limit = 20): Promise<TrendingForecast[]> {
        const now = Date.now();
        if (cache && now - cache.fetchedAt < TTL_MS && cache.limit >= limit) {
            return cache.data.slice(0, limit);
        }
        if (inflight) {
            const data = await inflight;
            return data.slice(0, limit);
        }
        inflight = trendingRepository.fetchFresh(Math.max(limit, 20));
        try {
            const data = await inflight;
            cache = { fetchedAt: Date.now(), limit: Math.max(limit, 20), data };
            return data.slice(0, limit);
        } finally {
            inflight = null;
        }
    },

    async fetchFresh(limit: number): Promise<TrendingForecast[]> {
        try {
            const url = `https://gamma-api.polymarket.com/markets?limit=${limit}&active=true&closed=false&order=volumeNum&ascending=false`;
            const response = await fetch(url);
            
            if (!response.ok) {
                throw new Error(`Polymarket API error: ${response.statusText}`);
            }
            
            const markets = (await response.json()) as any[];
            
            return markets.map((market: any) => {
                let probability = 0.5;
                try {
                    if (market.outcomePrices) {
                        const prices = JSON.parse(market.outcomePrices);
                        if (Array.isArray(prices) && prices.length > 0) {
                            probability = parseFloat(prices[0]);
                        }
                    }
                } catch (e) {
                    // Ignore parse errors
                }
                
                return {
                    id: market.id,
                    question: market.question,
                    popularityScore: parseFloat(market.volume24hr || market.volume || '0'),
                    probability: probability,
                    tags: ['Polymarket'],
                    createdAt: new Date().toISOString(),
                    updatedAt: new Date().toISOString(),
                };
            });
        } catch (error) {
            console.error('Failed to fetch from Polymarket, falling back to Firestore mock data:', error);
            
            // Fallback to Firestore
            const snapshot = await collectionRef('trendingForecasts')
                .orderBy('popularityScore', 'desc')
                .limit(limit)
                .get();

            return snapshot.docs.map((doc) => {
                const data = doc.data();
                return {
                    id: doc.id,
                    ...data,
                    popularityScore: data.popularityScore,
                    createdAt: toISOString(data.createdAt),
                    updatedAt: toISOString(data.updatedAt),
                };
            });
        }
    }
};
