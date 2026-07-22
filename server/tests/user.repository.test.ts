import { describe, it, expect, beforeEach, vi } from 'vitest';

// Hoisted mocks: vi.mock factories cannot reference outer-scope variables.
// vi.hoisted runs before imports and is the supported way to share mocks
// between the factory and the test body.
const mocks = vi.hoisted(() => {
    const mockUserRef = { id: 'user-abc-123' };
    const mockUsersCollection = {
        doc: vi.fn(() => mockUserRef),
    };

    const collectionRefMock = vi.fn((name: string) => {
        if (name === 'users') return mockUsersCollection;
        throw new Error(`Unexpected collection in test: ${name}`);
    });

    const fixedTimestamp = {
        toDate: () => new Date('2026-07-20T00:00:00.000Z'),
    };

    // Stand-in Firestore document store. `runTransaction` reads and writes
    // through it so a test can observe the value actually persisted.
    const store: { data: Record<string, unknown> | null } = { data: null };

    const transactionGetMock = vi.fn(async () => ({
        exists: store.data !== null,
        data: () => store.data,
    }));
    const transactionSetMock = vi.fn(
        (_ref: unknown, value: Record<string, unknown>, _opts: unknown) => {
            store.data = { ...(store.data ?? {}), ...value };
        }
    );

    const runTransactionMock = vi.fn(
        async (fn: (tx: unknown) => Promise<unknown>) =>
            fn({ get: transactionGetMock, set: transactionSetMock })
    );

    return {
        mockUserRef,
        mockUsersCollection,
        collectionRefMock,
        fixedTimestamp,
        store,
        transactionGetMock,
        transactionSetMock,
        runTransactionMock,
    };
});

vi.mock('../src/services/firebase.service.js', () => ({
    collectionRef: mocks.collectionRefMock,
    now: () => mocks.fixedTimestamp,
    runTransaction: mocks.runTransactionMock,
    batch: vi.fn(),
    toISOString: (ts: { toDate?: () => Date } | null | undefined) =>
        ts?.toDate?.()?.toISOString() ?? null,
}));

const { userRepository } = await import('../src/repositories/user.repository.js');
const { AppError } = await import('../src/middleware/error.js');

const CURRENT_MONTH = '2026-07';

describe('userRepository.incrementUsage', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-07-20T12:00:00.000Z'));
        mocks.store.data = {
            plan: 'free',
            monthlyForecastsUsed: 0,
            usageMonth: CURRENT_MONTH,
        };
    });

    it('runs the read-modify-write inside a Firestore transaction', async () => {
        await userRepository.incrementUsage('user-abc-123');

        expect(mocks.runTransactionMock).toHaveBeenCalledTimes(1);
        expect(mocks.transactionGetMock).toHaveBeenCalledWith(mocks.mockUserRef);
        expect(mocks.transactionSetMock).toHaveBeenCalledTimes(1);
    });

    it('increments the count and stamps the current usage month', async () => {
        await userRepository.incrementUsage('user-abc-123');

        expect(mocks.store.data).toMatchObject({
            monthlyForecastsUsed: 1,
            usageMonth: CURRENT_MONTH,
        });
    });

    it('resets the count when the stored usage month has rolled over', async () => {
        mocks.store.data = {
            plan: 'free',
            monthlyForecastsUsed: 3,
            usageMonth: '2026-06',
        };

        await userRepository.incrementUsage('user-abc-123');

        expect(mocks.store.data).toMatchObject({
            monthlyForecastsUsed: 1,
            usageMonth: CURRENT_MONTH,
        });
    });

    it('rejects a free user already at the limit and writes nothing', async () => {
        mocks.store.data = {
            plan: 'free',
            monthlyForecastsUsed: 3,
            usageMonth: CURRENT_MONTH,
        };

        await expect(userRepository.incrementUsage('user-abc-123')).rejects.toMatchObject({
            statusCode: 403,
            code: 'PLAN_LIMIT_EXCEEDED',
        });
        expect(mocks.transactionSetMock).not.toHaveBeenCalled();
        expect(mocks.store.data.monthlyForecastsUsed).toBe(3);
    });

    it('lets a premium user past the free limit', async () => {
        mocks.store.data = {
            plan: 'premium',
            monthlyForecastsUsed: 50,
            usageMonth: CURRENT_MONTH,
        };

        await userRepository.incrementUsage('user-abc-123');

        expect(mocks.store.data.monthlyForecastsUsed).toBe(51);
    });

    it('throws NOT_FOUND when the user document is missing', async () => {
        mocks.store.data = null;

        await expect(userRepository.incrementUsage('user-abc-123')).rejects.toBeInstanceOf(AppError);
        expect(mocks.transactionSetMock).not.toHaveBeenCalled();
    });

    // KG-C-10c: findById no longer persists the expiry downgrade on read, so
    // incrementUsage must evaluate it live. An expired cancelled-premium user is
    // treated as free here regardless of the stored `plan`.
    it('enforces the free limit on an expired cancelled-premium user', async () => {
        mocks.store.data = {
            plan: 'premium',
            cancelAtPeriodEnd: true,
            planExpiresAt: '2026-07-01T00:00:00.000Z', // before the fake system time
            monthlyForecastsUsed: 3,
            usageMonth: CURRENT_MONTH,
        };

        await expect(userRepository.incrementUsage('user-abc-123')).rejects.toMatchObject({
            code: 'PLAN_LIMIT_EXCEEDED',
        });
        expect(mocks.transactionSetMock).not.toHaveBeenCalled();
    });

    it('persists the downgrade to free when charging an expired premium user', async () => {
        mocks.store.data = {
            plan: 'premium',
            cancelAtPeriodEnd: true,
            planExpiresAt: '2026-07-01T00:00:00.000Z',
            monthlyForecastsUsed: 1,
            usageMonth: CURRENT_MONTH,
        };

        await userRepository.incrementUsage('user-abc-123');

        expect(mocks.store.data).toMatchObject({
            plan: 'free',
            cancelAtPeriodEnd: false,
            monthlyForecastsUsed: 2,
        });
    });

    it('does not downgrade a premium user whose plan has not expired', async () => {
        mocks.store.data = {
            plan: 'premium',
            cancelAtPeriodEnd: true,
            planExpiresAt: '2026-08-01T00:00:00.000Z', // after the fake system time
            monthlyForecastsUsed: 50,
            usageMonth: CURRENT_MONTH,
        };

        await userRepository.incrementUsage('user-abc-123');

        expect(mocks.store.data).toMatchObject({
            plan: 'premium',
            monthlyForecastsUsed: 51,
        });
    });

    // The regression this fix exists for (KG-C-9). Serialising two calls through
    // the same store is what the transaction guarantees; the pre-fix
    // read-then-write let both callers observe the same starting count.
    it('does not let two sequential calls share a starting count', async () => {
        mocks.store.data = {
            plan: 'free',
            monthlyForecastsUsed: 1,
            usageMonth: CURRENT_MONTH,
        };

        await userRepository.incrementUsage('user-abc-123');
        await userRepository.incrementUsage('user-abc-123');

        expect(mocks.store.data.monthlyForecastsUsed).toBe(3);

        // The fourth forecast must now be refused.
        await expect(userRepository.incrementUsage('user-abc-123')).rejects.toMatchObject({
            code: 'PLAN_LIMIT_EXCEEDED',
        });
    });
});
