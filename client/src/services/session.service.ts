import { apiRequest } from '../lib/api';
import { db } from '../lib/firebase';
import {
    collection,
    onSnapshot,
    orderBy,
    query,
    type QueryDocumentSnapshot,
    type Unsubscribe,
} from 'firebase/firestore';
import type { AgentEvent, AgentEventStatus, AgentEventType, KeyFactor, ReasoningStep, SuggestedAction } from '../types';

export type SessionStatus = 'queued' | 'claimed' | 'running' | 'done' | 'failed' | 'awaiting_clarification';

export interface ClarificationCandidate {
    id: string;                                  // canonical market id
    label: string;                               // human-readable
    source: 'polymarket' | 'kalshi';
    description: string;                         // resolution criteria / longer context
    matchConfidence: number;                     // 0-1
}

export interface SessionListItem {
    id: string;
    userId: string;
    question: string;
    title: string | null;
    status: SessionStatus;
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
    userId?: string | null;
    meta: {
        model?: string;
        tokensIn?: number;
        tokensOut?: number;
        runId?: string;
    } | null;
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
    impact: 'positive' | 'negative' | 'neutral' | null;
    impactLabel: string | null;
    isKeyEvidence: boolean;
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

export interface SessionResult {
    sessionId: string;
    userId: string;
    finalProbability: number;
    confidence: number;
    marketComparison: { source: string; value: number }[];
    summaryMarkdown: string | null;
    createdAt: string;
    updatedAt: string;
    confidenceLabel: 'High Confidence' | 'Medium Confidence' | 'Low Confidence' | null;
    consensusStrength: 'Strong' | 'Moderate' | 'Weak' | null;
    evidenceVolumeLabel: 'High' | 'Medium' | 'Low' | null;
    bottomLineAnswer: string | null;
    detailedExplanation: string | null;
    marketProbability: number | null;
    marketComparisonInsight: string | null;
    sentimentAnalysisInsight: string | null;
    evidenceFeedSummary: string | null;
    keyFactors: KeyFactor[];
    whatIDidntFind: string[];
    reasoningChain: ReasoningStep[];
    suggestedActions: SuggestedAction[];
    generatedAt: string | null;
    agentVersion: string | null;
    tier: 'tier_1' | 'tier_2' | null;
}

export interface PredictionPoint {
    id: string;
    ts: string;
    probability: number;
    confidence: number;
    reasonType: 'news' | 'market' | 'model_update';
    evidenceIds: string[];
}

export interface SessionDetail {
    session: SessionListItem;
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
    meta?: {
        model?: string;
        tokensIn?: number;
        tokensOut?: number;
        runId?: string;
    };
}

// Demo session fixtures previously short-circuited the API on failure and
// silently returned fake data. Removed (Task 3 of the restoration plan)
// so real users see real errors and a real empty state.

export interface ClarifySessionInput {
    chosenCandidateId: string | null;
}

function toDateValue(value: unknown): Date {
    if (value && typeof value === 'object' && 'toDate' in value && typeof value.toDate === 'function') {
        return value.toDate() as Date;
    }

    if (typeof value === 'string' || value instanceof Date) {
        const parsed = new Date(value);
        if (!Number.isNaN(parsed.getTime())) {
            return parsed;
        }
    }

    return new Date();
}

function mapAgentEventDoc(docSnapshot: QueryDocumentSnapshot): AgentEvent {
    const data = docSnapshot.data();

    return {
        eventId: typeof data.eventId === 'string' ? data.eventId : docSnapshot.id,
        sessionId: typeof data.sessionId === 'string' ? data.sessionId : '',
        sequence: typeof data.sequence === 'number' ? data.sequence : 0,
        timestamp: toDateValue(data.timestamp),
        parentMessageId: typeof data.parentMessageId === 'string' ? data.parentMessageId : null,
        type: data.type as AgentEventType,
        title: typeof data.title === 'string' ? data.title : 'Untitled event',
        description: typeof data.description === 'string' ? data.description : null,
        status: data.status as AgentEventStatus,
        durationMs: typeof data.durationMs === 'number' ? data.durationMs : null,
        payload: data.payload && typeof data.payload === 'object' ? (data.payload as Record<string, unknown>) : null,
    };
}

function mapSessionMessageDoc(docSnapshot: QueryDocumentSnapshot): SessionMessage {
    const data = docSnapshot.data();

    return {
        id: docSnapshot.id,
        role: data.role as SessionMessage['role'],
        content: typeof data.content === 'string' ? data.content : '',
        createdAt: toDateValue(data.createdAt).toISOString(),
        status: data.status === 'failed' ? 'failed' : data.status === 'sent' ? 'sent' : null,
        userId: typeof data.userId === 'string' ? data.userId : null,
        meta: data.meta && typeof data.meta === 'object' ? (data.meta as SessionMessage['meta']) : null,
    };
}

export async function fetchSessions(): Promise<SessionListItem[]> {
    return apiRequest<SessionListItem[]>('/sessions');
}

export async function fetchSessionDetail(sessionId: string): Promise<SessionDetail> {
    return apiRequest<SessionDetail>(`/sessions/${sessionId}`);
}

export async function createSession(input: CreateSessionInput): Promise<SessionListItem> {
    return apiRequest<SessionListItem>('/sessions', {
        method: 'POST',
        body: input,
    });
}

export async function addSessionMessage(
    sessionId: string,
    input: CreateMessageInput
): Promise<SessionMessage> {
    return apiRequest<SessionMessage>(`/sessions/${sessionId}/messages`, {
        method: 'POST',
        body: input,
    });
}

export async function clarifySession(
    sessionId: string,
    input: ClarifySessionInput
): Promise<SessionListItem> {
    return apiRequest<SessionListItem>(`/sessions/${sessionId}/clarify`, {
        method: 'POST',
        body: input,
    });
}

export function subscribeToAgentEvents(
    sessionId: string,
    handlers: {
        onData: (events: AgentEvent[]) => void;
        onError?: (error: Error) => void;
    }
): Unsubscribe {
    const eventsQuery = query(
        collection(db, 'sessions', sessionId, 'agentEvents'),
        orderBy('sequence', 'asc')
    );

    return onSnapshot(
        eventsQuery,
        (snapshot) => {
            handlers.onData(snapshot.docs.map(mapAgentEventDoc));
        },
        (error) => {
            handlers.onError?.(error);
        }
    );
}

export function subscribeToSessionMessages(
    sessionId: string,
    handlers: {
        onData: (messages: SessionMessage[]) => void;
        onError?: (error: Error) => void;
    }
): Unsubscribe {
    const messagesQuery = query(
        collection(db, 'sessions', sessionId, 'messages'),
        orderBy('createdAt', 'asc')
    );

    return onSnapshot(
        messagesQuery,
        (snapshot) => {
            handlers.onData(snapshot.docs.map(mapSessionMessageDoc));
        },
        (error) => {
            handlers.onError?.(error);
        }
    );
}

export async function deleteSession(sessionId: string): Promise<void> {
    await apiRequest<{ id: string }>(`/sessions/${sessionId}`, {
        method: 'DELETE',
    });
}
