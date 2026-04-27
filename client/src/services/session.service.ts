import { apiRequest } from '../lib/api';
import {
    mockChatMessages,
    mockCurrentPrediction,
    mockSentimentData,
    mockSessions,
    mockTimelineEvents,
} from '../data/mockData';

export type SessionStatus = 'draft' | 'running' | 'done' | 'failed';

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
    summaryMarkdown: string;
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

const DEMO_USER_ID = 'demo-user-001';
const nowIso = () => new Date().toISOString();

let demoSessions: SessionListItem[] = mockSessions.map((session) => ({
    id: session.id,
    userId: DEMO_USER_ID,
    question: session.question,
    title: session.question,
    status: 'done',
    latestProbability: session.probability / 100,
    latestConfidence: 0.84,
    followEnabled: true,
    isFollowing: false,
    canonicalKey: null,
    errorCode: null,
    errorMessage: null,
    createdAt: session.lastUpdated.toISOString(),
    updatedAt: session.lastUpdated.toISOString(),
    lastActivityAt: session.lastUpdated.toISOString(),
}));

const demoMessagesBySessionId = new Map<string, SessionMessage[]>(
    demoSessions.map((session) => [
        session.id,
        mockChatMessages.map((message) => ({
            id: `${session.id}-${message.id}`,
            role: message.role,
            content: message.content,
            createdAt: message.timestamp.toISOString(),
            status: 'sent',
            meta: null,
        })),
    ])
);

function buildDemoSessionDetail(sessionId: string): SessionDetail {
    const session = demoSessions.find((item) => item.id === sessionId) ?? demoSessions[0];
    const probability = session.latestProbability ?? mockCurrentPrediction.probability / 100;

    return {
        session,
        messages: demoMessagesBySessionId.get(session.id) ?? [],
        predictionSeries: mockSentimentData.map((_point, index) => ({
            id: `${session.id}-prediction-${index + 1}`,
            ts: new Date(2026, 0, index + 1).toISOString(),
            probability,
            confidence: 0.84,
            reasonType: index % 2 === 0 ? 'news' : 'model_update',
            evidenceIds: [],
        })),
        evidence: mockTimelineEvents.map((event) => ({
            id: event.id,
            type: event.sourceType,
            title: event.title,
            snippet: event.description,
            url: null,
            publishedAt: new Date(`2026 ${event.date}`).toISOString(),
            sourceId: null,
            score: event.isKeyEvidence ? 0.9 : 0.55,
            createdAt: nowIso(),
            impact: event.impact,
            impactLabel: event.impactLabel ?? null,
            isKeyEvidence: event.isKeyEvidence ?? false,
        })),
        result: {
            sessionId: session.id,
            userId: session.userId,
            finalProbability: probability,
            confidence: 0.84,
            marketComparison: [{ source: 'Polymarket', value: 0.685 }],
            summaryMarkdown: mockCurrentPrediction.explanation,
            createdAt: session.createdAt,
            updatedAt: session.updatedAt,
            confidenceLabel: 'High Confidence',
            consensusStrength: 'Moderate',
            evidenceVolumeLabel: 'High',
            bottomLineAnswer: mockCurrentPrediction.explanation,
            detailedExplanation: mockCurrentPrediction.explanation,
            marketProbability: mockCurrentPrediction.marketProbability
                ? mockCurrentPrediction.marketProbability / 100
                : null,
            marketComparisonInsight: 'Demo market data is close to the model estimate.',
            sentimentAnalysisInsight: 'Expert sentiment is moving ahead of public sentiment.',
            evidenceFeedSummary: 'Demo evidence is assembled from local data.',
        },
        sentimentTimeSeries: mockSentimentData.map((point, index) => ({
            id: `${session.id}-sentiment-${index + 1}`,
            ts: new Date(2026, 0, index + 1).toISOString(),
            date: point.date,
            expertSentiment: point.expertSentiment / 100,
            expertUpper: point.expertUpper ? point.expertUpper / 100 : null,
            expertLower: point.expertLower ? point.expertLower / 100 : null,
            publicSentiment: point.publicSentiment / 100,
            createdAt: nowIso(),
        })),
    };
}

export async function fetchSessions(): Promise<SessionListItem[]> {
    try {
        return await apiRequest<SessionListItem[]>('/sessions');
    } catch (error) {
        console.warn('Using demo sessions because the authenticated API is unavailable.', error);
        return demoSessions;
    }
}

export async function fetchSessionDetail(sessionId: string): Promise<SessionDetail> {
    try {
        return await apiRequest<SessionDetail>(`/sessions/${sessionId}`);
    } catch (error) {
        console.warn('Using demo session detail because the authenticated API is unavailable.', error);
        return buildDemoSessionDetail(sessionId);
    }
}

export async function createSession(input: CreateSessionInput): Promise<SessionListItem> {
    try {
        return await apiRequest<SessionListItem>('/sessions', {
            method: 'POST',
            body: input,
        });
    } catch (error) {
        console.warn('Creating a demo session because the authenticated API is unavailable.', error);
        const createdAt = nowIso();
        const created: SessionListItem = {
            id: `local-${Date.now()}`,
            userId: DEMO_USER_ID,
            question: input.question,
            title: input.title ?? input.question,
            status: 'done',
            latestProbability: 0.52,
            latestConfidence: 0.72,
            followEnabled: true,
            isFollowing: false,
            canonicalKey: null,
            errorCode: null,
            errorMessage: null,
            createdAt,
            updatedAt: createdAt,
            lastActivityAt: createdAt,
        };
        demoSessions = [created, ...demoSessions];
        demoMessagesBySessionId.set(created.id, []);
        return created;
    }
}

export async function addSessionMessage(
    sessionId: string,
    input: CreateMessageInput
): Promise<SessionMessage> {
    try {
        return await apiRequest<SessionMessage>(`/sessions/${sessionId}/messages`, {
            method: 'POST',
            body: input,
        });
    } catch (error) {
        console.warn('Saving a demo message because the authenticated API is unavailable.', error);
        const created: SessionMessage = {
            id: `local-message-${Date.now()}`,
            role: input.role,
            content: input.content,
            createdAt: nowIso(),
            status: 'sent',
            meta: input.meta ?? null,
        };
        const messages = demoMessagesBySessionId.get(sessionId) ?? [];
        demoMessagesBySessionId.set(sessionId, [...messages, created]);
        return created;
    }
}

export async function deleteSession(sessionId: string): Promise<void> {
    try {
        await apiRequest<{ id: string }>(`/sessions/${sessionId}`, {
            method: 'DELETE',
        });
    } catch (error) {
        console.warn('Deleting a demo session because the authenticated API is unavailable.', error);
        demoSessions = demoSessions.filter((session) => session.id !== sessionId);
        demoMessagesBySessionId.delete(sessionId);
    }
}
