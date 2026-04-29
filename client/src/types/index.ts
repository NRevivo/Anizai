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
    keyFactors?: KeyFactor[];
    whatIDidntFind?: string[];
    reasoningChain?: ReasoningStep[];
    suggestedActions?: SuggestedAction[];
    generatedAt?: Date | null;
    agentVersion?: string | null;
    tier?: 'tier_1' | 'tier_2' | null;
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
    status?: 'pending' | 'sent' | 'failed';
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
    evidenceId?: string | null;
    date: string;
    timestamp?: Date | null;
    title: string;
    sourceType: 'news' | 'expert' | 'social' | 'market';
    source?: string | null;
    origin?: string | null;
    sourceDomain?: string | null;
    snippet?: string | null;
    url?: string | null;
    fetchedAt?: Date | null;
    relevanceScore?: number | null;
    credibilityTier?: string | null;
    recencyWeight?: number | null;
    usedInAnswer?: boolean | null;
    impactOnForecast?: string | null;
    justification?: string | null;
    rank?: number | null;
    impact: 'positive' | 'negative' | 'neutral';
    impactLabel?: string;
    isKeyEvidence?: boolean;
    description: string;
}

export interface SuggestedAction {
    id: string;
    label: string;
    prompt?: string;
    icon?: string;
}

export interface KeyFactor {
    rank: number;
    title: string;
    explanation: string;
    direction: 'supports' | 'opposes' | 'uncertain';
    weight: number;
    supportingEvidenceIds: string[];
}

export interface ReasoningStep {
    sequence: number;
    description: string;
    outcome: string;
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
