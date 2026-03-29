import { collectionRef, toISOString } from '../services/firebase.service.js';

export interface TrendingForecast {
    id: string;
    popularityScore: number;
    question?: string;
    probability?: number;
    tags?: string[];
    [key: string]: unknown;
}

export const trendingRepository = {
    /**
     * Get top trending forecasts by popularity score
     * Fetches real active markets from Polymarket Gamma API, falls back to Firestore mock data
     */
    async getTopTrending(limit = 20): Promise<TrendingForecast[]> {
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
