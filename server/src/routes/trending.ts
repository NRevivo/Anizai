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

export default router;
