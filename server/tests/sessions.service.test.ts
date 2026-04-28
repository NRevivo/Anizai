import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppError } from '../src/middleware/error.js';

const mocks = vi.hoisted(() => {
    return {
        listSessions: vi.fn(),
        getSession: vi.fn(),
        getSessionResult: vi.fn(),
        getMessages: vi.fn(),
        getPredictionSeries: vi.fn(),
        getEvidence: vi.fn(),
        getSentimentTimeSeries: vi.fn(),
        findRecentSessionByIdempotencyKey: vi.fn(),
        createSession: vi.fn(),
        requeueClarifiedSession: vi.fn(),
        addMessage: vi.fn(),
        deleteSession: vi.fn(),
        incrementUsage: vi.fn(),
    };
});

vi.mock('../src/repositories/session.repository.js', () => ({
    sessionRepository: {
        listSessions: mocks.listSessions,
        getSession: mocks.getSession,
        getSessionResult: mocks.getSessionResult,
        getMessages: mocks.getMessages,
        getPredictionSeries: mocks.getPredictionSeries,
        getEvidence: mocks.getEvidence,
        getSentimentTimeSeries: mocks.getSentimentTimeSeries,
        findRecentSessionByIdempotencyKey: mocks.findRecentSessionByIdempotencyKey,
        createSession: mocks.createSession,
        requeueClarifiedSession: mocks.requeueClarifiedSession,
        addMessage: mocks.addMessage,
        deleteSession: mocks.deleteSession,
    },
}));

vi.mock('../src/services/users.service.js', () => ({
    incrementUsage: mocks.incrementUsage,
}));

import * as sessionsService from '../src/services/sessions.service.js';

describe('sessionsService.createSession', () => {
    beforeEach(() => {
        Object.values(mocks).forEach((mockFn) => mockFn.mockReset());
    });

    it('returns a structured plan-limit error before creating a session', async () => {
        mocks.findRecentSessionByIdempotencyKey.mockResolvedValue(null);
        mocks.incrementUsage.mockRejectedValue(
            new AppError(
                "You've used your free forecasts this month",
                403,
                'PLAN_LIMIT_EXCEEDED',
                {
                    used: 3,
                    limit: 3,
                    planTier: 'free',
                    resetAt: '2026-05-01T00:00:00.000Z',
                }
            )
        );

        await expect(
            sessionsService.createSession('user-free', {
                question: 'Will inflation fall below 3% this year?',
                idempotencyKey: '55555555-5555-4555-8555-555555555555',
            })
        ).rejects.toMatchObject({
            statusCode: 403,
            code: 'PLAN_LIMIT_EXCEEDED',
            details: {
                used: 3,
                limit: 3,
                planTier: 'free',
                resetAt: '2026-05-01T00:00:00.000Z',
            },
        });

        expect(mocks.createSession).not.toHaveBeenCalled();
        expect(mocks.findRecentSessionByIdempotencyKey).toHaveBeenCalledTimes(1);
    });

    it('re-queues an awaiting_clarification session with the selected candidate', async () => {
        const clarificationCandidate = {
            id: 'market-123',
            label: 'Fed rate cut by July 2026',
            source: 'polymarket' as const,
            description: 'Market resolving to yes if the Fed cuts rates by July 2026.',
            matchConfidence: 0.82,
        };

        const session = {
            id: 'session-1',
            userId: 'user-1',
            question: 'Will the Fed cut rates by July 2026?',
            title: null,
            status: 'awaiting_clarification' as const,
            latestProbability: null,
            latestConfidence: null,
            followEnabled: false,
            isFollowing: false,
            canonicalKey: null,
            errorCode: null,
            errorMessage: 'Clarification required',
            clarificationCandidates: [clarificationCandidate],
            createdAt: '2026-04-28T12:00:00.000Z',
            updatedAt: '2026-04-28T12:00:00.000Z',
            lastActivityAt: '2026-04-28T12:00:00.000Z',
        };
        const requeuedSession = {
            ...session,
            status: 'queued' as const,
            canonicalKey: clarificationCandidate.id,
            errorMessage: null,
            clarificationCandidates: null,
            updatedAt: '2026-04-28T12:05:00.000Z',
            lastActivityAt: '2026-04-28T12:05:00.000Z',
        };

        mocks.getSession.mockResolvedValue(session);
        mocks.requeueClarifiedSession.mockResolvedValue(requeuedSession);

        const result = await sessionsService.clarifySession('session-1', 'user-1', {
            chosenCandidateId: clarificationCandidate.id,
        });

        expect(mocks.requeueClarifiedSession).toHaveBeenCalledWith(session, clarificationCandidate);
        expect(result).toEqual(requeuedSession);
    });
});
