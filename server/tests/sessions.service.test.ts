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
});
