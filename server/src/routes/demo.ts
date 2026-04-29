import { Router } from 'express';
import type { ApiSuccessResponse } from '../types/api.js';
import { AppError } from '../middleware/error.js';
import { sessionRepository } from '../repositories/session.repository.js';
import { userRepository } from '../repositories/user.repository.js';

const router = Router();

/**
 * GET /demo/sessions
 * List all sessions for demo user (for testing without auth)
 * REMOVE IN PRODUCTION
 */
router.get('/demo/sessions', async (_req, res, next) => {
    try {
        const DEMO_USER_ID = 'demo-user-001';

        const sessions = await sessionRepository.listSessions(DEMO_USER_ID, 50);

        const response: ApiSuccessResponse = {
            data: sessions,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * GET /demo/sessions/:id
 * Get session details for demo user (for testing without auth)
 * REMOVE IN PRODUCTION
 */
router.get('/demo/sessions/:id', async (req, res, next) => {
    try {
        const DEMO_USER_ID = 'demo-user-001';
        const sessionId = req.params.id;

        // Get session
        const session = await sessionRepository.getSession(sessionId);

        if (!session || session.userId !== DEMO_USER_ID) {
            throw new AppError('Session not found', 404, 'NOT_FOUND');
        }

        // Get subcollections
        const [
            messages,
            predictionSeries,
            evidence,
            result
        ] = await Promise.all([
            sessionRepository.getMessages(sessionId),
            sessionRepository.getPredictionSeries(sessionId),
            sessionRepository.getEvidence(sessionId, 20),
            sessionRepository.getSessionResult(sessionId) // Only gets result logic from DB
        ]);

        const response: ApiSuccessResponse = {
            data: {
                session,
                messages,
                predictionSeries,
                evidence,
                result,
            },
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * GET /demo/user
 * Get demo user profile (for testing)
 * REMOVE IN PRODUCTION
 */
router.get('/demo/user', async (_req, res, next) => {
    try {
        const DEMO_USER_ID = 'demo-user-001';

        const user = await userRepository.findById(DEMO_USER_ID);

        if (!user) {
            throw new AppError('User not found', 404, 'NOT_FOUND');
        }

        const response: ApiSuccessResponse = {
            data: user,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

export default router;

