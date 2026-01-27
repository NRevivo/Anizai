import { Router } from 'express';
import { authMiddleware } from '../middleware/auth.js';
import * as usersService from '../services/users.service.js';
import type { ApiSuccessResponse, AuthUser } from '../types/api.js';

const router = Router();

/**
 * GET /me
 * Get current user's profile
 * Creates user document if first login
 */
router.get('/me', authMiddleware, async (req, res, next) => {
    try {
        const authUser = req.user as AuthUser;

        const profile = await usersService.getPublicProfile(authUser.uid, authUser.email ?? '');

        const response: ApiSuccessResponse = {
            data: profile,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

export default router;
