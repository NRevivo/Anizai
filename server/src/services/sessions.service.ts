/**
 * Sessions Service
 * Firestore operations for sessions and related subcollections
 */

import { AppError } from '../middleware/error.js';
import { sessionRepository } from '../repositories/session.repository.js';
import * as usersService from './users.service.js';

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

export interface ClarificationCandidate {
    id: string;                                  // canonical market id
    label: string;                               // human-readable
    source: 'polymarket' | 'kalshi';
    description: string;                         // resolution criteria / longer context
    matchConfidence: number;                     // 0-1
}

export interface Session {
    id: string;
    userId: string;
    question: string;
    title: string | null;
    status: 'queued' | 'claimed' | 'running' | 'done' | 'failed' | 'awaiting_clarification';
    latestProbability: number | null;
    latestConfidence: number | null;
    followEnabled: boolean;
    isFollowing: boolean;
    canonicalKey: string | null;
    errorCode: string | null;
    errorMessage: string | null;
    clarificationCandidates: ClarificationCandidate[] | null;
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
    // New: Impact classification
    impact: 'positive' | 'negative' | 'neutral' | null;
    impactLabel: string | null;
    isKeyEvidence: boolean;
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
    // New: Confidence & Consensus metrics
    confidenceLabel: 'High Confidence' | 'Medium Confidence' | 'Low Confidence' | null;
    consensusStrength: 'Strong' | 'Moderate' | 'Weak' | null;
    evidenceVolumeLabel: 'High' | 'Medium' | 'Low' | null;
    // New: Executive summary components
    bottomLineAnswer: string | null;
    detailedExplanation: string | null;
    // New: Market comparison insights
    marketProbability: number | null;
    marketComparisonInsight: string | null;
    // New: Sentiment analysis insights
    sentimentAnalysisInsight: string | null;
    // New: Evidence feed insights
    evidenceFeedSummary: string | null;
}

export interface SentimentDataPoint {
    id: string;
    ts: string;
    date: string;
    expertSentiment: number;
    expertUpper: number | null;
    expertLower: number | null;
    publicSentiment: number;
    createdAt: string;
}


export interface SessionDetail {
    session: Session;
    messages: SessionMessage[];
    predictionSeries: PredictionPoint[];
    evidence: Evidence[];
    result: SessionResult | null;
    sentimentTimeSeries: SentimentDataPoint[];
}

export interface CreateSessionInput {
    question: string;
    title?: string;
    idempotencyKey: string;
}

export interface CreateMessageInput {
    role: 'user' | 'assistant' | 'system';
    content: string;
    meta?: SessionMessage['meta'];
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────


// ─────────────────────────────────────────────────────────────
// Service Methods
// ─────────────────────────────────────────────────────────────

/**
 * List sessions for a user, ordered by lastActivityAt desc
 * Requires index: sessions (userId ASC, lastActivityAt DESC)
 */
export async function listSessions(userId: string, limit = 50): Promise<Session[]> {
    return sessionRepository.listSessions(userId, limit);
}

/**
 * Get session by ID with ownership check
 */
export async function getSession(sessionId: string, userId: string): Promise<Session> {
    const session = await sessionRepository.getSession(sessionId);

    if (!session || session.userId !== userId) {
        throw new AppError('Session not found', 404, 'NOT_FOUND');
    }

    return session;
}

/**
 * Get session result by sessionId with userId verification
 * Returns null if not found
 */
export async function getSessionResult(sessionId: string, userId: string): Promise<SessionResult | null> {
    const result = await sessionRepository.getSessionResult(sessionId);

    if (!result) {
        return null;
    }

    if (result.userId !== userId) {
        throw new AppError('Session result not found', 404, 'NOT_FOUND');
    }

    return result;
}


/**
 * Get full session detail with subcollections
 * Requires indexes for subcollection ordering
 */
export async function getSessionDetail(sessionId: string, userId: string): Promise<SessionDetail> {
    // Get and verify session ownership
    const session = await getSession(sessionId, userId);

    const [
        messages,
        predictionSeries,
        evidence,
        sentimentTimeSeries,
        result
    ] = await Promise.all([
        sessionRepository.getMessages(sessionId),
        sessionRepository.getPredictionSeries(sessionId),
        sessionRepository.getEvidence(sessionId),
        sessionRepository.getSentimentTimeSeries(sessionId),
        getSessionResult(sessionId, userId)
    ]);

    return {
        session,
        messages,
        predictionSeries,
        evidence,
        result,
        sentimentTimeSeries,
    };
}

/**
 * Create a new session
 */
export async function createSession(userId: string, input: CreateSessionInput): Promise<Session> {
    const existing = await sessionRepository.findRecentSessionByIdempotencyKey(
        userId,
        input.idempotencyKey
    );

    if (existing) {
        return existing;
    }

    // Check and commit increment usage synchronously prior to document writing
    await usersService.incrementUsage(userId);

    const existingAfterUsage = await sessionRepository.findRecentSessionByIdempotencyKey(
        userId,
        input.idempotencyKey
    );

    if (existingAfterUsage) {
        return existingAfterUsage;
    }

    return sessionRepository.createSession(userId, input);
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

    return sessionRepository.addMessage(sessionId, input);
}

/**
 * Delete a session and all related documents for the authenticated user
 */
export async function deleteSession(sessionId: string, userId: string): Promise<void> {
    // Verify ownership first
    await getSession(sessionId, userId);
    await sessionRepository.deleteSession(sessionId);
}
