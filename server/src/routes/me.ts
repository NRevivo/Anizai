import { Router } from 'express';
import { z } from 'zod';
import { validationError } from '../middleware/error.js';
import * as usersService from '../services/users.service.js';
import type { ApiSuccessResponse, AuthUser } from '../types/api.js';

// Auth is enforced at the app level for the whole `/me` path prefix
// (see server.ts), so every route here is protected by construction (KG-C-10a).
const router = Router();

const updatePlanSchema = z.object({
    plan: z.enum(['free', 'premium']),
});

/**
 * GET /me
 * Get current user's profile
 * Creates user document if first login
 */
router.get('/me', async (req, res, next) => {
    try {
        const authUser = req.user as AuthUser;
        const email = authUser.email ?? `${authUser.uid}@anonymous.invalid`;

        const profile = await usersService.getPublicProfile(authUser.uid, email, authUser.name ?? null);

        const response: ApiSuccessResponse = {
            data: profile,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * PATCH /me/plan
 * Update current user's plan
 */
router.patch('/me/plan', async (req, res, next) => {
    try {
        const authUser = req.user as AuthUser;
        const parsed = updatePlanSchema.safeParse(req.body);

        if (!parsed.success) {
            throw validationError(parsed.error);
        }

        const profile = await usersService.updatePlan(
            authUser.uid,
            parsed.data.plan
        );

        const response: ApiSuccessResponse = {
            data: profile,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

export default router;
