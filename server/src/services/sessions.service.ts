/**
 * Sessions Service
 * Firestore operations for sessions and related subcollections
 */

import { firestore } from '../lib/firebase.js';
import { Timestamp } from 'firebase-admin/firestore';
import { AppError } from '../middleware/error.js';

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

export interface Session {
    id: string;
    userId: string;
    question: string;
    title: string | null;
    status: 'draft' | 'running' | 'done' | 'failed';
    latestProbability: number | null;
    latestConfidence: number | null;
    followEnabled: boolean;
    isFollowing: boolean;
    canonicalKey: string | null;
    errorCode: string | null;
    errorMessage: string | null;
    createdAt: string;
    updatedAt: string;
    lastActivityAt: string;
}

export interface SessionMessage {
    id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    createdAt: string;
    status: 'sent' | 'failed' | null;
    meta: {
        model?: string;
        tokensIn?: number;
        tokensOut?: number;
        runId?: string;
    } | null;
}

export interface PredictionPoint {
    id: string;
    ts: string;
    probability: number;
    confidence: number;
    reasonType: 'news' | 'market' | 'model_update';
    evidenceIds: string[];
}

export interface Evidence {
    id: string;
    type: 'news' | 'social' | 'expert' | 'market';
    title: string;
    snippet: string;
    url: string | null;
    publishedAt: string | null;
    sourceId: string | null;
    score: number;
    createdAt: string;
}

export interface SessionResult {
    sessionId: string;
    userId: string;
    finalProbability: number;
    confidence: number;
    marketComparison: { source: string; value: number }[];
    summaryMarkdown: string;
    createdAt: string;
    updatedAt: string;
}

export interface SessionDetail {
    session: Session;
    messages: SessionMessage[];
    predictionSeries: PredictionPoint[];
    evidence: Evidence[];
    result: SessionResult | null;
}

export interface CreateSessionInput {
    question: string;
    title?: string;
}

export interface CreateMessageInput {
    role: 'user' | 'assistant' | 'system';
    content: string;
    meta?: SessionMessage['meta'];
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function toISOString(timestamp: FirebaseFirestore.Timestamp | null | undefined): string | null {
    return timestamp?.toDate?.()?.toISOString() ?? null;
}

// ─────────────────────────────────────────────────────────────
// Service Methods
// ─────────────────────────────────────────────────────────────

/**
 * List sessions for a user, ordered by lastActivityAt desc
 * Requires index: sessions (userId ASC, lastActivityAt DESC)
 */
export async function listSessions(userId: string, limit = 50): Promise<Session[]> {
    const snapshot = await firestore
        .collection('sessions')
        .where('userId', '==', userId)
        .orderBy('lastActivityAt', 'desc')
        .limit(limit)
        .get();

    return snapshot.docs.map((doc) => {
        const data = doc.data();
        return {
            id: doc.id,
            userId: data.userId,
            question: data.question,
            title: data.title ?? null,
            status: data.status,
            latestProbability: data.latestProbability ?? null,
            latestConfidence: data.latestConfidence ?? null,
            followEnabled: data.followEnabled ?? false,
            isFollowing: data.isFollowing ?? false,
            canonicalKey: data.canonicalKey ?? null,
            errorCode: data.errorCode ?? null,
            errorMessage: data.errorMessage ?? null,
            createdAt: toISOString(data.createdAt) ?? '',
            updatedAt: toISOString(data.updatedAt) ?? '',
            lastActivityAt: toISOString(data.lastActivityAt) ?? '',
        };
    });
}

/**
 * Get session by ID with ownership check
 */
export async function getSession(sessionId: string, userId: string): Promise<Session> {
    const doc = await firestore.collection('sessions').doc(sessionId).get();

    if (!doc.exists) {
        throw new AppError('Session not found', 404, 'NOT_FOUND');
    }

    const data = doc.data()!;

    if (data.userId !== userId) {
        throw new AppError('Session not found', 404, 'NOT_FOUND');
    }

    return {
        id: doc.id,
        userId: data.userId,
        question: data.question,
        title: data.title ?? null,
        status: data.status,
        latestProbability: data.latestProbability ?? null,
        latestConfidence: data.latestConfidence ?? null,
        followEnabled: data.followEnabled ?? false,
        isFollowing: data.isFollowing ?? false,
        canonicalKey: data.canonicalKey ?? null,
        errorCode: data.errorCode ?? null,
        errorMessage: data.errorMessage ?? null,
        createdAt: toISOString(data.createdAt) ?? '',
        updatedAt: toISOString(data.updatedAt) ?? '',
        lastActivityAt: toISOString(data.lastActivityAt) ?? '',
    };
}

/**
 * Get full session detail with subcollections
 * Requires indexes for subcollection ordering
 */
export async function getSessionDetail(sessionId: string, userId: string): Promise<SessionDetail> {
    // Get and verify session ownership
    const session = await getSession(sessionId, userId);

    const sessionRef = firestore.collection('sessions').doc(sessionId);

    // Get messages (ordered by createdAt ASC)
    const messagesSnapshot = await sessionRef.collection('messages').orderBy('createdAt', 'asc').get();

    const messages: SessionMessage[] = messagesSnapshot.docs.map((doc) => {
        const data = doc.data();
        return {
            id: doc.id,
            role: data.role,
            content: data.content,
            createdAt: toISOString(data.createdAt) ?? '',
            status: data.status ?? null,
            meta: data.meta ?? null,
        };
    });

    // Get prediction series (ordered by ts ASC)
    const seriesSnapshot = await sessionRef.collection('predictionSeries').orderBy('ts', 'asc').get();

    const predictionSeries: PredictionPoint[] = seriesSnapshot.docs.map((doc) => {
        const data = doc.data();
        return {
            id: doc.id,
            ts: toISOString(data.ts) ?? '',
            probability: data.probability,
            confidence: data.confidence,
            reasonType: data.reasonType,
            evidenceIds: data.evidenceIds ?? [],
        };
    });

    // Get evidence (ordered by createdAt DESC)
    const evidenceSnapshot = await sessionRef
        .collection('evidence')
        .orderBy('createdAt', 'desc')
        .limit(50)
        .get();

    const evidence: Evidence[] = evidenceSnapshot.docs.map((doc) => {
        const data = doc.data();
        return {
            id: doc.id,
            type: data.type,
            title: data.title,
            snippet: data.snippet,
            url: data.url ?? null,
            publishedAt: toISOString(data.publishedAt),
            sourceId: data.sourceId ?? null,
            score: data.score,
            createdAt: toISOString(data.createdAt) ?? '',
        };
    });

    // Get session result if exists
    const resultDoc = await firestore.collection('sessionResults').doc(sessionId).get();
    let result: SessionResult | null = null;

    if (resultDoc.exists) {
        const data = resultDoc.data()!;
        result = {
            sessionId: data.sessionId,
            userId: data.userId,
            finalProbability: data.finalProbability,
            confidence: data.confidence,
            marketComparison: data.marketComparison ?? [],
            summaryMarkdown: data.summaryMarkdown,
            createdAt: toISOString(data.createdAt) ?? '',
            updatedAt: toISOString(data.updatedAt) ?? '',
        };
    }

    return {
        session,
        messages,
        predictionSeries,
        evidence,
        result,
    };
}

/**
 * Create a new session
 */
export async function createSession(userId: string, input: CreateSessionInput): Promise<Session> {
    const now = Timestamp.now();

    const sessionData = {
        userId,
        question: input.question,
        title: input.title ?? null,
        status: 'draft' as const,
        latestProbability: null,
        latestConfidence: null,
        followEnabled: false,
        isFollowing: false,
        canonicalKey: null,
        errorCode: null,
        errorMessage: null,
        createdAt: now,
        updatedAt: now,
        lastActivityAt: now,
    };

    const docRef = await firestore.collection('sessions').add(sessionData);

    return {
        id: docRef.id,
        ...sessionData,
        createdAt: now.toDate().toISOString(),
        updatedAt: now.toDate().toISOString(),
        lastActivityAt: now.toDate().toISOString(),
    };
}

/**
 * Add a message to a session and update lastActivityAt
 */
export async function addMessage(
    sessionId: string,
    userId: string,
    input: CreateMessageInput
): Promise<SessionMessage> {
    // Verify ownership first
    await getSession(sessionId, userId);

    const now = Timestamp.now();

    const messageData = {
        role: input.role,
        content: input.content,
        createdAt: now,
        status: 'sent' as const,
        meta: input.meta ?? null,
    };

    const sessionRef = firestore.collection('sessions').doc(sessionId);

    // Add message and update session in a batch
    const batch = firestore.batch();

    const messageRef = sessionRef.collection('messages').doc();
    batch.set(messageRef, messageData);

    batch.update(sessionRef, {
        lastActivityAt: now,
        updatedAt: now,
    });

    await batch.commit();

    return {
        id: messageRef.id,
        ...messageData,
        createdAt: now.toDate().toISOString(),
    };
}
