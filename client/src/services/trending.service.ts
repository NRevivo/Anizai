import { apiRequest } from '../lib/api';

/** Mirrors `server/src/repositories/trending.repository.ts`. Nothing enforces that
 *  the two stay in sync (KG-C-1) — change both together. */
export interface TrendingOutcome {
    label: string;
    probability: number;
}

export interface TrendingForecast {
    id: string;
    title: string;
    /** Canonical Polymarket event page — rendered as the link on each row. */
    url: string;
    /** Yes probability for a binary event; `null` for a multi-outcome field, which
     *  has no single probability. Branch on it — do not default it to a number. */
    probability: number | null;
    /** Binary: one Yes entry. Multi-outcome: the leading legs, price-desc. */
    outcomes: TrendingOutcome[];
    /** 24-hour traded volume in USD — the value the feed is ranked by. */
    volume24h: number;
    /** Total markets in the event; 1 means binary. */
    marketCount: number;
}

export async function fetchTrendingForecasts(limit = 20): Promise<TrendingForecast[]> {
    try {
        const data = await apiRequest<TrendingForecast[]>(
            `/trending?limit=${encodeURIComponent(String(limit))}`,
            { requireAuth: false }
        );
        return data.slice(0, limit);
    } catch (error) {
        // Trending is a secondary surface: degrade to an empty list so the
        // consumers fall through to their existing empty states, rather than
        // showing fabricated data as if it were real (KG-C-5).
        //
        // Deliberately resolves instead of rethrowing — App.tsx `enterDashboard`
        // awaits this inside a Promise.all whose rejection blocks dashboard
        // entry, so a trending outage must not take the whole dashboard down.
        console.error('Failed to load trending forecasts; rendering empty.', error);
        return [];
    }
}
