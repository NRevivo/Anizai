import { Router } from 'express';
import type { ApiSuccessResponse } from '../types/api.js';

const router = Router();

/**
 * GET /
 * Root endpoint
 */
router.get('/', (_req, res) => {
    const response: ApiSuccessResponse = {
        data: {
            ok: true,
            name: 'anizai-server',
        },
    };

    res.json(response);
});

export default router;
