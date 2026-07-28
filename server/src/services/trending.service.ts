import { AppError } from '../middleware/error.js';
import {
    trendingRepository,
    type TrendingForecast,
    type TrendingMarket,
} from '../repositories/trending.repository.js';

export async function getTopTrending(limit = 20): Promise<TrendingForecast[]> {
    return trendingRepository.getTopTrending(limit);
}

/**
 * Free-text search across Polymarket events, filtered to the pipeline's topics.
 *
 * A query shorter than the repository's minimum resolves to `[]` rather than an
 * error — an empty or one-character box is a normal UI state, not a failure.
 */
export async function searchTrending(query: string, limit = 20): Promise<TrendingForecast[]> {
    return trendingRepository.searchEvents(query, limit);
}

/**
 * Every selectable market for one trending event.
 *
 * Throws 404 when the event does not exist upstream. An event that exists but has
 * no selectable markets resolves to `[]` — a real answer, not an error, and the
 * caller renders "nothing to pick" rather than a failure.
 */
export async function getEventMarkets(eventId: string): Promise<TrendingMarket[]> {
    const markets = await trendingRepository.getEventMarkets(eventId);
    if (markets === null) {
        throw new AppError('Trending event not found', 404, 'NOT_FOUND');
    }
    return markets;
}
