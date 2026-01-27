import { Router } from 'express';
import { firestore } from '../lib/firebase.js';
import type { ApiSuccessResponse } from '../types/api.js';

const router = Router();

/**
 * GET /trending
 * List trending forecasts (public endpoint)
 */
router.get('/trending', async (_req, res, next) => {
    try {
        const snapshot = await firestore
            .collection('trendingForecasts')
            .orderBy('popularityScore', 'desc')
            .limit(20)
            .get();

        const forecasts = snapshot.docs.map((doc) => ({
            id: doc.id,
            ...doc.data(),
            createdAt: doc.data().createdAt?.toDate?.()?.toISOString(),
            updatedAt: doc.data().updatedAt?.toDate?.()?.toISOString(),
        }));

        const response: ApiSuccessResponse = {
            data: forecasts,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

export default router;
