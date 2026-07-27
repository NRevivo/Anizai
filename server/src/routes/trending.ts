import { Router } from 'express';
import * as trendingService from '../services/trending.service.js';
import type { ApiSuccessResponse } from '../types/api.js';

const router = Router();

/**
 * GET /trending
 * List trending forecasts (public endpoint)
 */
router.get('/trending', async (req, res, next) => {
    try {
        const requested = Number.parseInt(String(req.query.limit ?? ''), 10);
        const limit = Number.isFinite(requested) && requested > 0
            ? Math.min(requested, 100)
            : 20;
        const forecasts = await trendingService.getTopTrending(limit);

        const response: ApiSuccessResponse = {
            data: forecasts,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * GET /trending/:id/markets
 * Every selectable market for one trending event (public endpoint).
 *
 * Split out of `GET /trending` so the list — which the landing page fetches
 * unauthenticated on every visit — does not carry the full field for every event.
 * Fetched when the user opens a picker. Binary events already ship their single
 * market inline and never need this.
 */
router.get('/trending/:id/markets', async (req, res, next) => {
    try {
        const markets = await trendingService.getEventMarkets(String(req.params.id));

        const response: ApiSuccessResponse = {
            data: markets,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

export default router;
