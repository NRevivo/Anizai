import { describe, it, expect, beforeEach, vi } from 'vitest';

// Hoisted mocks: vi.mock factories cannot reference outer-scope variables.
// vi.hoisted runs before imports and is the supported way to share mocks
// between the factory and the test body.
const mocks = vi.hoisted(() => {
    // Firestore resolves FieldValue.serverTimestamp() to the commit time, so the
    // sentinel that goes into the write is NOT the value that comes back out —
    // the repository re-reads the doc. `get()` models that resolved read.
    const serverTimestampSentinel = { __sentinel: 'serverTimestamp' };
    const resolvedCreatedAt = {
        toDate: () => new Date('2026-04-27T12:34:56.000Z'),
    };
    const messageGetMock = vi.fn(async () => ({
        get: (field: string) => (field === 'createdAt' ? resolvedCreatedAt : undefined),
    }));
    const mockMessagesDocRef = { id: 'message-doc-123', get: messageGetMock };
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
        serverTimestampSentinel,
        resolvedCreatedAt,
        messageGetMock,
    };
});

vi.mock('../src/services/firebase.service.js', () => ({
    batch: mocks.batchMock,
    collectionRef: mocks.collectionRefMock,
    now: vi.fn(() => mocks.fixedTimestamp),
    serverTimestamp: vi.fn(() => mocks.serverTimestampSentinel),
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
        mocks.messageGetMock.mockClear();
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
                'conditionId',
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
            // Written unconditionally so consumers read one shape. This call
            // passed no conditionId — the freeform path — so it must be an
            // explicit null, not an absent key.
            conditionId: null,
        });

        // Explicit absence checks — these are the legacy fields we removed.
        expect(forecastQueryData).not.toHaveProperty('resultRef');
        expect(forecastQueryData).not.toHaveProperty('metadata');
        expect(forecastQueryData).not.toHaveProperty('updatedAt');
        expect(forecastQueryData).not.toHaveProperty('query');
    });

    it('carries conditionId onto both documents when the question came from a market', async () => {
        // The deterministic join key. `question` is the market's verbatim text,
        // so a text match would also work — this spares the pipeline that, and
        // is the whole point of threading the id through from the picker.
        await sessionRepository.createSession('user-7', {
            question: 'Will there be no change in Fed interest rates after the September 2026 meeting?',
            idempotencyKey: '33333333-3333-4333-8333-333333333333',
            conditionId: '0x723822eb2b143cee54c0bd7c1efba322b21f0051984c266df8879c394f1011c0',
        });

        const sessionData = mocks.setMock.mock.calls[0][1];
        const forecastQueryData = mocks.setMock.mock.calls[1][1];

        expect(forecastQueryData).toMatchObject({
            conditionId: '0x723822eb2b143cee54c0bd7c1efba322b21f0051984c266df8879c394f1011c0',
        });

        // Also persisted on the session, so requeueClarifiedSession can carry it
        // forward. Without it there, a clarified session requeues with a null id
        // and silently loses the join.
        expect(sessionData).toMatchObject({
            conditionId: '0x723822eb2b143cee54c0bd7c1efba322b21f0051984c266df8879c394f1011c0',
        });
    });

    it('writes a null conditionId for a freeform question', async () => {
        // Optional must mean optional: nothing may break without it.
        await sessionRepository.createSession('user-8', {
            question: 'Will my startup raise a Series A this year?',
            idempotencyKey: '44444444-4444-4444-8444-444444444444',
        });

        expect(mocks.setMock.mock.calls[0][1]).toMatchObject({ conditionId: null });
        expect(mocks.setMock.mock.calls[1][1]).toMatchObject({ conditionId: null });
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
            lastActivityAt: mocks.serverTimestampSentinel,
            updatedAt: mocks.serverTimestampSentinel,
        }));
        expect(mocks.commitMock).toHaveBeenCalledTimes(1);
    });

    // The messages subcollection has two writers ordered by the same field:
    // this BFF and the data-pipeline agent (which uses SERVER_TIMESTAMP). If the
    // BFF stamps its own host clock, a reply can sort above the question it
    // answers whenever the two clocks disagree.
    it('stamps message createdAt with the Firestore server clock, not the host clock', async () => {
        await sessionRepository.addMessage('session-abc-123', 'user-99', {
            role: 'user',
            content: 'Why is the confidence moderate?',
        });

        const writtenMessage = mocks.setMock.mock.calls[0][1];
        expect(writtenMessage.createdAt).toBe(mocks.serverTimestampSentinel);
        expect(writtenMessage.createdAt).not.toBe(mocks.fixedTimestamp);
    });

    it('returns the resolved commit timestamp, never the unresolved sentinel', async () => {
        // The client sorts its optimistic message by this value, so it has to be
        // a real ISO string read back after the commit.
        const created = await sessionRepository.addMessage('session-abc-123', 'user-99', {
            role: 'user',
            content: 'Why is the confidence moderate?',
        });

        expect(mocks.messageGetMock).toHaveBeenCalledTimes(1);
        expect(created.createdAt).toBe('2026-04-27T12:34:56.000Z');
        expect(created.id).toBe('message-doc-123');
        expect(created.status).toBe('sent');
    });

    it('reads the committed timestamp only after the batch commits', async () => {
        await sessionRepository.addMessage('session-abc-123', 'user-99', {
            role: 'user',
            content: 'Why is the confidence moderate?',
        });

        // Reading before the commit would resolve the sentinel to null.
        const commitOrder = mocks.commitMock.mock.invocationCallOrder[0];
        const readOrder = mocks.messageGetMock.mock.invocationCallOrder[0];
        expect(commitOrder).toBeLessThan(readOrder);
    });
});
