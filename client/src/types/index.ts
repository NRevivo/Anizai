export type SessionStatus = 'queued' | 'claimed' | 'running' | 'done' | 'failed' | 'awaiting_clarification';

export interface ClarificationCandidate {
    id: string;
    label: string;
    source: 'polymarket' | 'kalshi';
    description: string;
    matchConfidence: number;
}

// Core prediction types
export interface Prediction {
    id: string;
    question: string;
    probability: number; // 0-1 float
    confidenceIndex: number; // 0-1 float
    status: SessionStatus;
    explanation: string;
    marketProbability?: number;
    errorMessage?: string | null;
    clarificationCandidates?: ClarificationCandidate[] | null;
    createdAt: Date;
    updatedAt: Date;
}

export interface PredictionSession {
    id: string;
    question: string;
    probability: number | null;
    status: SessionStatus;
    errorMessage?: string | null;
    clarificationCandidates?: ClarificationCandidate[] | null;
    lastUpdated: Date;
}

export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
}

export interface SentimentDataPoint {
    date: string;
    expertSentiment: number; // 0-1 float
    expertUpper?: number;
    expertLower?: number;
    publicSentiment: number; // 0-1 float
}

export interface TimelineEvent {
    id: string;
    date: string;
    title: string;
    sourceType: 'news' | 'expert' | 'social';
    impact: 'positive' | 'negative' | 'neutral';
    impactLabel?: string;
    isKeyEvidence?: boolean;
    description: string;
}

export interface SuggestedAction {
    id: string;
    label: string;
    icon?: string;
}

export type AgentEventType =
    | 'vault_query'
    | 'vault_query_result'
    | 'sufficiency_check'
    | 'reactive_search'
    | 'reactive_search_result'
    | 'evidence_rated'
    | 'synthesis_started'
    | 'synthesis_complete'
    | 'clarification_needed'
    | 'followup_started'
    | 'context_loaded'
    | 'followup_search'
    | 'followup_response_complete'
    | 'error';

export type AgentEventStatus = 'in_progress' | 'complete' | 'failed';

export interface AgentEvent {
    eventId: string;
    sessionId: string;
    sequence: number;
    timestamp: Date;
    parentMessageId: string | null;
    type: AgentEventType;
    title: string;
    description: string | null;
    status: AgentEventStatus;
    durationMs: number | null;
    payload: Record<string, unknown> | null;
}
