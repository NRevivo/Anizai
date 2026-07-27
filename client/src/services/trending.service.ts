import { apiRequest } from '../lib/api';

/** Mirrors `server/src/repositories/trending.repository.ts`. Nothing enforces that
 *  the two stay in sync (KG-C-1) — change both together. */
export interface TrendingOutcome {
    label: string;
    probability: number;
}

/** One selectable market inside an event — the unit a forecast is created against. */
export interface TrendingMarket {
    /** Polymarket condition id; the pipeline's join key for this market. */
    conditionId: string;
    /** The real market question. Submit this verbatim — never the event title. */
    question: string;
    /** Short leg label ("Abiy Ahmed"); falls back to `question` for binary events. */
    groupItemTitle: string;
    /** Yes-side probability, 0–1. Multiply by 100 only at render time. */
    probability: number;
    /** 24-hour traded volume for this market, in USD. */
    volume24h: number;
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
    /**
     * Every selectable market, probability-descending — no dedup, no cap. This is
     * what the selection step chooses from; `outcomes` is only a display summary.
     *
     * ⚠ Usually shorter than `marketCount`, which still counts inactive placeholder
     * legs. Show `markets.length` when the number describes what the user can pick.
     */
    markets: TrendingMarket[];
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
