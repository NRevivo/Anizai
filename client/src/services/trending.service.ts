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
     * Selectable markets, probability-descending — no dedup, no cap.
     *
     * ⚠️ **CONDITIONALLY POPULATED. Empty here means "not loaded at this layer",
     * NEVER "this event has no markets".**
     *   - Binary events (`marketCount === 1`) ship their single market inline, so a
     *     binary card can submit on click with no second round-trip.
     *   - Multi-outcome events arrive with `markets: []` — the field is omitted
     *     from the list payload and must be fetched via `fetchTrendingMarkets(id)`.
     *
     * Never render "no markets available" off this being empty. Branch on
     * `marketCount` to decide whether to fetch, and treat an empty array from
     * `fetchTrendingMarkets` as the only real "none exist" signal.
     *
     * ⚠️ Also usually shorter than `marketCount`, which still counts inactive
     * placeholder legs. Show `markets.length` when the number describes what the
     * user can actually pick.
     */
    markets: TrendingMarket[];
    /** 24-hour traded volume in USD — the value the feed is ranked by. */
    volume24h: number;
    /** Total markets in the event; 1 means binary. */
    marketCount: number;
    /**
     * True when the outcomes are mutually exclusive (a candidate field — exactly
     * one can resolve Yes). False when they are independent, overlapping
     * propositions (the "ladder" shape: strike ladders, date series).
     *
     * From Polymarket's `negRisk` flag, not guessed from the title. Affects only
     * how the picker words itself — both shapes are a flat list to choose from.
     */
    mutuallyExclusive: boolean;
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

/**
 * Fetch the full selectable field for one trending event.
 *
 * Needed because `TrendingForecast.markets` is only populated inline for binary
 * events — see the warning on that field. Call this when opening a picker for a
 * multi-outcome event.
 *
 * Unlike `fetchTrendingForecasts`, this **rethrows**. It backs a direct user
 * action, so the caller must be able to tell "still loading" from "failed" and
 * show a retry — silently resolving to `[]` here would render an empty picker
 * that looks like "this event has no markets", which is exactly the fabricated
 * -certainty failure the empty-list degrade avoids elsewhere.
 */
export async function fetchTrendingMarkets(eventId: string): Promise<TrendingMarket[]> {
    return apiRequest<TrendingMarket[]>(
        `/trending/${encodeURIComponent(eventId)}/markets`,
        { requireAuth: false }
    );
}
