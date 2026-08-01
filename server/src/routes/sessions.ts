import { Router } from 'express';
import { z } from 'zod';
import { validationError } from '../middleware/error.js';
import * as sessionsService from '../services/sessions.service.js';
import type { ApiSuccessResponse, AuthUser } from '../types/api.js';

// Auth is enforced at the app level for the whole `/sessions` path prefix
// (see server.ts), so every route on this router is protected by construction
// — a new handler added below cannot accidentally ship public (KG-C-10a).
const router = Router();

// ─────────────────────────────────────────────────────────────
// Validation Schemas
// ─────────────────────────────────────────────────────────────

const createSessionSchema = z.object({
    question: z.string().min(1).max(1000),
    title: z.string().max(200).optional(),
    idempotencyKey: z.string().trim().min(1).uuid(),
    // Polymarket condition id, present only when the question came from a market
    // pick. `nullish` rather than `optional`: the client sends `undefined` on the
    // freeform path but an explicit `null` is equally valid, and rejecting either
    // would break freeform submission — which has no market and never will.
    // Not `.uuid()`; a condition id is a 0x-prefixed 32-byte hash, not a UUID.
    conditionId: z.string().trim().min(1).max(200).nullish(),
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

const clarifySessionSchema = z.object({
    chosenCandidateId: z.string().trim().min(1).nullable(),
});

// ─────────────────────────────────────────────────────────────
// Routes
// ─────────────────────────────────────────────────────────────

/**
 * GET /sessions
 * List all sessions for the authenticated user
 */
router.get('/sessions', async (req, res, next) => {
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
router.get('/sessions/:id', async (req, res, next) => {
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
 * GET /sessions/:id/predictionSeries
 * The market's price history for one session.
 *
 * Separate from `GET /sessions/:id` so ~683 documents stay off the blocking path
 * of every session open — the chart is below the fold and most readers never
 * reach it. Fetched when the card scrolls into view. See the service for the
 * full rationale.
 */
router.get('/sessions/:id/predictionSeries', async (req, res, next) => {
    try {
        const user = req.user as AuthUser;
        const sessionId = req.params.id as string;

        const series = await sessionsService.getPredictionSeries(sessionId, user.uid);

        const response: ApiSuccessResponse = {
            data: series,
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
router.post('/sessions', async (req, res, next) => {
    try {
        const user = req.user as AuthUser;

        const parsed = createSessionSchema.safeParse(req.body);
        if (!parsed.success) {
            throw validationError(parsed.error);
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
router.post('/sessions/:id/messages', async (req, res, next) => {
    try {
        const user = req.user as AuthUser;
        const sessionId = req.params.id as string;

        const parsed = createMessageSchema.safeParse(req.body);
        if (!parsed.success) {
            throw validationError(parsed.error);
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
 * POST /sessions/:id/clarify
 * Submit a clarification choice for a queued follow-up request
 */
router.post('/sessions/:id/clarify', async (req, res, next) => {
    try {
        const user = req.user as AuthUser;
        const sessionId = req.params.id as string;

        const parsed = clarifySessionSchema.safeParse(req.body);
        if (!parsed.success) {
            throw validationError(parsed.error);
        }

        const session = await sessionsService.clarifySession(sessionId, user.uid, parsed.data);

        const response: ApiSuccessResponse = {
            data: session,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * POST /sessions/:id/retry
 * Replace a failed session with a new attempt using the original question.
 * The failed session is hard-deleted (subcollections + sessionResults +
 * forecastQueries) and a fresh session is enqueued for the agent. Only valid
 * when the session's status is `failed`.
 */
router.post('/sessions/:id/retry', async (req, res, next) => {
    try {
        const user = req.user as AuthUser;
        const sessionId = req.params.id as string;

        const session = await sessionsService.retryFailedSession(sessionId, user.uid);

        const response: ApiSuccessResponse = {
            data: session,
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
router.delete('/sessions/:id', async (req, res, next) => {
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
