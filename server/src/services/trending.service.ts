import { trendingRepository, type TrendingForecast } from '../repositories/trending.repository.js';

export async function getTopTrending(limit = 20): Promise<TrendingForecast[]> {
    return trendingRepository.getTopTrending(limit);
}
