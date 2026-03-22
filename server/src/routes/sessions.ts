import { Router } from 'express';
import { z } from 'zod';
import { authMiddleware } from '../middleware/auth.js';
import { AppError } from '../middleware/error.js';
import * as sessionsService from '../services/sessions.service.js';
import type { ApiSuccessResponse, AuthUser } from '../types/api.js';

const router = Router();

// ─────────────────────────────────────────────────────────────
// Validation Schemas
// ─────────────────────────────────────────────────────────────

const createSessionSchema = z.object({
    question: z.string().min(1).max(1000),
    title: z.string().max(200).optional(),
});

const createMessageSchema = z.object({
    role: z.enum(['user', 'assistant', 'system']),
    content: z.string().min(1).max(50000),
    meta: z
        .object({
            model: z.string().optional(),
            tokensIn: z.number().optional(),
            tokensOut: z.number().optional(),
            runId: z.string().optional(),
        })
        .optional(),
});

// ─────────────────────────────────────────────────────────────
// Routes
// ─────────────────────────────────────────────────────────────

/**
 * GET /sessions
 * List all sessions for the authenticated user
 */
router.get('/sessions', authMiddleware, async (req, res, next) => {
    try {
        const user = req.user as AuthUser;
        const sessions = await sessionsService.listSessions(user.uid);

        const response: ApiSuccessResponse = {
            data: sessions,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * GET /sessions/:id
 * Get session details with messages, predictions, and evidence
 */
router.get('/sessions/:id', authMiddleware, async (req, res, next) => {
    try {
        const user = req.user as AuthUser;
        const sessionId = req.params.id as string;

        const detail = await sessionsService.getSessionDetail(sessionId, user.uid);

        const response: ApiSuccessResponse = {
            data: detail,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * POST /sessions
 * Create a new session
 */
router.post('/sessions', authMiddleware, async (req, res, next) => {
    try {
        const user = req.user as AuthUser;

        const parsed = createSessionSchema.safeParse(req.body);
        if (!parsed.success) {
            throw new AppError('Invalid request body', 400, 'VALIDATION_ERROR');
        }

        const session = await sessionsService.createSession(user.uid, parsed.data);

        const response: ApiSuccessResponse = {
            data: session,
        };

        res.status(201).json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * POST /sessions/:id/messages
 * Add a message to a session
 */
router.post('/sessions/:id/messages', authMiddleware, async (req, res, next) => {
    try {
        const user = req.user as AuthUser;
        const sessionId = req.params.id as string;

        const parsed = createMessageSchema.safeParse(req.body);
        if (!parsed.success) {
            throw new AppError('Invalid request body', 400, 'VALIDATION_ERROR');
        }

        const message = await sessionsService.addMessage(sessionId, user.uid, parsed.data);

        const response: ApiSuccessResponse = {
            data: message,
        };

        res.status(201).json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * DELETE /sessions/:id
 * Delete a session and all related records
 */
router.delete('/sessions/:id', authMiddleware, async (req, res, next) => {
    try {
        const user = req.user as AuthUser;
        const sessionId = req.params.id as string;

        await sessionsService.deleteSession(sessionId, user.uid);

        const response: ApiSuccessResponse = {
            data: { id: sessionId },
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

export default router;
