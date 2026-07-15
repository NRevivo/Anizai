/**
 * Sessions Service
 * Firestore operations for sessions and related subcollections
 */

import { randomUUID } from 'node:crypto';
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
    // 'answered' is written by the hub when it flips a user message from 'sent'
    // once its assistant reply lands (same write batch).
    status: 'sent' | 'failed' | 'answered' | null;
    userId?: string | null;
    // Present on hub-written assistant messages: the doc id of the user message
    // this reply answers. Lets the frontend link answer <-> question.
    replyToMessageId?: string | null;
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
    evidenceId: string | null;
    sourceType: string | null;
    origin: string | null;
    title: string;
    snippet: string;
    url: string | null;
    source: string | null;
    sourceDomain: string | null;
    publishedAt: string | null;
    fetchedAt: string | null;
    sourceId: string | null;
    score: number;
    relevanceScore: number | null;
    credibilityTier: string | null;
    recencyWeight: number | null;
    usedInAnswer: boolean | null;
    impactOnForecast: string | null;
    justification: string | null;
    rank: number | null;
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
    summaryMarkdown: string | null;
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
    keyFactors: KeyFactor[];
    whatIDidntFind: string[];
    reasoningChain: ReasoningStep[];
    suggestedActions: SuggestedAction[];
    generatedAt: string | null;
    agentVersion: string | null;
    tier: 'tier_1' | 'tier_2' | null;
}

// Mirrors the canonical pipeline shape (data-pipeline/agent/schemas.py).
// The repository pass-through reads these directly from Firestore — no
// rename layer between us and the agent.
export interface KeyFactor {
    label: string;
    description: string;
    direction: 'increases' | 'decreases';
    weight: number;
    evidence_ids: string[];
}

export interface ReasoningStep {
    step: number;
    title: string;
    description: string;
}

export interface SuggestedAction {
    id: string;
    label: string;
    prompt: string;
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

export interface ClarifySessionInput {
    chosenCandidateId: string | null;
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
 * Get session result by sessionId, authorizing via the parent session.
 *
 * `sessionResults/<id>` documents are keyed by sessionId and do not carry
 * a `userId` field (the pipeline writes them without one). Ownership
 * lives on `sessions/<id>`, so we verify there before reading the result
 * by doc id.
 *
 * Returns null if the result doc doesn't exist; throws 404 if the parent
 * session is missing or owned by a different user.
 */
export async function getSessionResult(sessionId: string, userId: string): Promise<SessionResult | null> {
    // Throws 404 unless the requester owns the parent session.
    await getSession(sessionId, userId);

    return sessionRepository.getSessionResult(sessionId);
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

export async function clarifySession(
    sessionId: string,
    userId: string,
    input: ClarifySessionInput
): Promise<Session> {
    const session = await getSession(sessionId, userId);

    if (session.status !== 'awaiting_clarification') {
        throw new AppError('Session is not awaiting clarification', 409, 'INVALID_SESSION_STATUS');
    }

    const candidates = session.clarificationCandidates ?? [];
    const selectedCandidate = input.chosenCandidateId === null
        ? null
        : candidates.find((candidate) => candidate.id === input.chosenCandidateId) ?? null;

    if (input.chosenCandidateId !== null && !selectedCandidate) {
        throw new AppError('Chosen clarification candidate is invalid', 400, 'INVALID_CLARIFICATION_CANDIDATE');
    }

    return sessionRepository.requeueClarifiedSession(session, selectedCandidate);
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

    return sessionRepository.addMessage(sessionId, userId, input);
}

/**
 * Delete a session and all related documents for the authenticated user
 */
export async function deleteSession(sessionId: string, userId: string): Promise<void> {
    // Verify ownership first
    await getSession(sessionId, userId);
    await sessionRepository.deleteSession(sessionId);
}

/**
 * Replace a failed session with a new attempt using the original question.
 * The failed session and all its subcollections are hard-deleted; a fresh
 * session is created and enqueued for the agent. Returns the new session.
 *
 * Delete-then-create order: if delete partially fails, the create still
 * happens so the user is never stuck without a retry session. Per Slice 12.
 */
export async function retryFailedSession(sessionId: string, userId: string): Promise<Session> {
    const session = await getSession(sessionId, userId);

    if (session.status !== 'failed') {
        throw new AppError('Retry is only valid on failed sessions', 400, 'INVALID_SESSION_STATUS');
    }

    const question = session.question?.trim();
    if (!question) {
        throw new AppError('Failed session is missing the original question', 400, 'MISSING_QUESTION');
    }

    try {
        await sessionRepository.deleteSession(sessionId);
    } catch (error) {
        // Per Slice 12: better to lose the old failed record than to leave
        // the user without a retry session. Log for operator visibility but
        // continue to the create step.
        console.warn(`[retryFailedSession] partial delete for ${sessionId}:`, error);
    }

    return createSession(userId, {
        question,
        title: session.title ?? undefined,
        idempotencyKey: randomUUID(),
    });
}
