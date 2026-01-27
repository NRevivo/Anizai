import { Router } from 'express';
import type { HealthResponse, ApiSuccessResponse } from '../types/api.js';

const router = Router();

/**
 * GET /health
 * Health check endpoint
 */
router.get('/health', (_req, res) => {
    const response: ApiSuccessResponse<HealthResponse> = {
        data: {
            ok: true,
            timestamp: new Date().toISOString(),
        },
    };

    res.json(response);
});

export default router;
