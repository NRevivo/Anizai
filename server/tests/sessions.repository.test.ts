import { describe, it, expect, beforeEach, vi } from 'vitest';

// Hoisted mocks: vi.mock factories cannot reference outer-scope variables.
// vi.hoisted runs before imports and is the supported way to share mocks
// between the factory and the test body.
const mocks = vi.hoisted(() => {
    const mockMessagesDocRef = { id: 'message-doc-123' };
    const mockMessagesCollection = {
        doc: vi.fn(() => mockMessagesDocRef),
    };
    const mockSessionRef: {
        id: string;
        collection: ReturnType<typeof vi.fn>;
    } = {
        id: 'session-abc-123',
        collection: vi.fn((name: string) => {
            if (name === 'messages') {
                return mockMessagesCollection;
            }
            throw new Error(`Unexpected subcollection in test: ${name}`);
        }),
    };
    const mockForecastQueryRef = { id: 'forecast-query-ref' };

    const mockSessionsCollection = {
        doc: vi.fn(() => mockSessionRef),
    };
    const mockForecastQueriesCollection = {
        doc: vi.fn(() => mockForecastQueryRef),
    };

    const setMock = vi.fn();
    const updateMock = vi.fn();
    const deleteMock = vi.fn();
    const commitMock = vi.fn(async () => undefined);

    const batchMock = vi.fn(() => ({
        set: setMock,
        update: updateMock,
        delete: deleteMock,
        commit: commitMock,
    }));

    const collectionRefMock = vi.fn((name: string) => {
        if (name === 'sessions') return mockSessionsCollection;
        if (name === 'forecastQueries') return mockForecastQueriesCollection;
        throw new Error(`Unexpected collection in test: ${name}`);
    });

    // Stand-in for Firestore Timestamp — supports .toDate().toISOString() chain
    // used in the repository return value.
    const fixedTimestamp = {
        toDate: () => new Date('2026-04-27T00:00:00.000Z'),
    };

    return {
        mockSessionRef,
        mockForecastQueryRef,
        mockMessagesDocRef,
        mockMessagesCollection,
        mockSessionsCollection,
        mockForecastQueriesCollection,
        setMock,
        updateMock,
        deleteMock,
        commitMock,
        batchMock,
        collectionRefMock,
        fixedTimestamp,
    };
});

vi.mock('../src/services/firebase.service.js', () => ({
    batch: mocks.batchMock,
    collectionRef: mocks.collectionRefMock,
    now: vi.fn(() => mocks.fixedTimestamp),
    toISOString: vi.fn(
        (ts: { toDate?: () => Date } | null | undefined) =>
            ts?.toDate?.()?.toISOString() ?? null
    ),
}));

// Import the unit under test AFTER vi.mock (vi.mock is hoisted by vitest).
import { sessionRepository } from '../src/repositories/session.repository.js';

describe('sessionRepository.createSession', () => {
    beforeEach(() => {
        mocks.setMock.mockClear();
        mocks.updateMock.mockClear();
        mocks.deleteMock.mockClear();
        mocks.commitMock.mockClear();
        mocks.batchMock.mockClear();
        mocks.mockSessionsCollection.doc.mockClear();
        mocks.mockForecastQueriesCollection.doc.mockClear();
        mocks.mockMessagesCollection.doc.mockClear();
        mocks.collectionRefMock.mockClear();
        mocks.mockSessionRef.collection.mockClear();
    });

    it('writes a session doc with status "queued" and null error/clarification fields', async () => {
        await sessionRepository.createSession('user-1', {
            question: 'Will X happen?',
            idempotencyKey: '11111111-1111-4111-8111-111111111111',
        });

        expect(mocks.setMock).toHaveBeenCalledTimes(2);

        const firstCall = mocks.setMock.mock.calls[0];
        const firstRef = firstCall[0];
        const sessionData = firstCall[1];

        expect(firstRef).toBe(mocks.mockSessionRef);
        expect(sessionData).toMatchObject({
            userId: 'user-1',
            question: 'Will X happen?',
            title: null,
            idempotencyKey: '11111111-1111-4111-8111-111111111111',
            status: 'queued',
            errorCode: null,
            errorMessage: null,
            clarificationCandidates: null,
            canonicalKey: null,
            latestProbability: null,
            latestConfidence: null,
            followEnabled: false,
            isFollowing: false,
        });
    });

    it('writes a forecastQueries doc with exact spec shape and no extra fields', async () => {
        await sessionRepository.createSession('user-42', {
            question: 'Will rates fall?',
            idempotencyKey: '22222222-2222-4222-8222-222222222222',
        });

        expect(mocks.setMock).toHaveBeenCalledTimes(2);

        // Doc id for forecastQueries must equal sessionRef.id (Option A: 1:1 lookup pattern).
        expect(mocks.mockForecastQueriesCollection.doc).toHaveBeenCalledWith(
            mocks.mockSessionRef.id
        );

        const secondCall = mocks.setMock.mock.calls[1];
        const secondRef = secondCall[0];
        const forecastQueryData = secondCall[1];

        expect(secondRef).toBe(mocks.mockForecastQueryRef);

        // Exact field set — no extras (no resultRef, metadata, updatedAt).
        expect(Object.keys(forecastQueryData).sort()).toEqual(
            [
                'claimedAt',
                'claimedBy',
                'createdAt',
                'queryId',
                'question',
                'sessionId',
                'status',
                'userId',
            ].sort()
        );

        expect(forecastQueryData).toMatchObject({
            sessionId: mocks.mockSessionRef.id,
            userId: 'user-42',
            question: 'Will rates fall?',
            status: 'pending',
            claimedAt: null,
            claimedBy: null,
        });

        // Explicit absence checks — these are the legacy fields we removed.
        expect(forecastQueryData).not.toHaveProperty('resultRef');
        expect(forecastQueryData).not.toHaveProperty('metadata');
        expect(forecastQueryData).not.toHaveProperty('updatedAt');
        expect(forecastQueryData).not.toHaveProperty('query');
    });

    it('queryId is a UUID v4 distinct from sessionId', async () => {
        await sessionRepository.createSession('user-1', {
            question: 'q',
            idempotencyKey: '33333333-3333-4333-8333-333333333333',
        });

        const forecastQueryData = mocks.setMock.mock.calls[1][1];
        const uuidV4Regex =
            /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

        expect(forecastQueryData.queryId).toMatch(uuidV4Regex);
        expect(forecastQueryData.queryId).not.toBe(forecastQueryData.sessionId);
        expect(forecastQueryData.queryId).not.toBe(mocks.mockSessionRef.id);
    });

    it('commits exactly once after both set calls', async () => {
        await sessionRepository.createSession('user-1', {
            question: 'q',
            idempotencyKey: '44444444-4444-4444-8444-444444444444',
        });

        expect(mocks.commitMock).toHaveBeenCalledTimes(1);
        expect(mocks.setMock).toHaveBeenCalledTimes(2);

        // Both set calls must precede the single commit call.
        const firstSetOrder = mocks.setMock.mock.invocationCallOrder[0];
        const secondSetOrder = mocks.setMock.mock.invocationCallOrder[1];
        const commitOrder = mocks.commitMock.mock.invocationCallOrder[0];

        expect(firstSetOrder).toBeLessThan(commitOrder);
        expect(secondSetOrder).toBeLessThan(commitOrder);
    });

    it('writes follow-up messages with user metadata into the session messages subcollection', async () => {
        await sessionRepository.addMessage('session-abc-123', 'user-99', {
            role: 'user',
            content: 'What changed after the last update?',
            meta: {
                runId: 'run-1',
            },
        });

        expect(mocks.mockSessionRef.collection).toHaveBeenCalledWith('messages');
        expect(mocks.mockMessagesCollection.doc).toHaveBeenCalledTimes(1);

        const firstSetCall = mocks.setMock.mock.calls[0];
        expect(firstSetCall[0]).toBe(mocks.mockMessagesDocRef);
        expect(firstSetCall[1]).toMatchObject({
            userId: 'user-99',
            role: 'user',
            content: 'What changed after the last update?',
            status: 'sent',
            meta: {
                runId: 'run-1',
            },
        });

        expect(mocks.updateMock).toHaveBeenCalledWith(mocks.mockSessionRef, expect.objectContaining({
            lastActivityAt: mocks.fixedTimestamp,
            updatedAt: mocks.fixedTimestamp,
        }));
        expect(mocks.commitMock).toHaveBeenCalledTimes(1);
    });
});
