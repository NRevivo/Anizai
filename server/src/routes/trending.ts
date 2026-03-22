import { Router } from 'express';
import * as trendingService from '../services/trending.service.js';
import type { ApiSuccessResponse } from '../types/api.js';

const router = Router();

/**
 * GET /trending
 * List trending forecasts (public endpoint)
 */
router.get('/trending', async (_req, res, next) => {
    try {
        const forecasts = await trendingService.getTopTrending(20);

        const response: ApiSuccessResponse = {
            data: forecasts,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

export default router;
