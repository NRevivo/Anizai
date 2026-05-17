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
    /** @deprecated Collapsed narrative fallback. New code should read bottomLineAnswer / detailedExplanation / summaryMarkdown directly. */
    explanation: string;
    bottomLineAnswer?: string | null;
    detailedExplanation?: string | null;
    summaryMarkdown?: string | null;
    confidenceLabel?: 'High Confidence' | 'Medium Confidence' | 'Low Confidence' | null;
    consensusStrength?: 'Strong' | 'Moderate' | 'Weak' | null;
    marketProbability?: number | null;
    marketComparisonInsight?: string | null;
    sentimentAnalysisInsight?: string | null;
    evidenceFeedSummary?: string | null;
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
    // Canonical impact rating from the agent's synthesis step. The legacy
    // `impact` field was removed — the pipeline only populates this one.
    impactOnForecast?: 'positive' | 'negative' | 'neutral' | null;
    justification?: string | null;
    rank?: number | null;
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

// Shape mirrors the pipeline's canonical KeyFactor (data-pipeline/agent/schemas.py).
// Snake_case is intentional — the pipeline emits these field names directly into
// Firestore and the server passes them through unchanged.
export interface KeyFactor {
    label: string;
    description: string;
    direction: 'increases' | 'decreases';
    weight: number;
    evidence_ids: string[];
}

// Mirrors data-pipeline/agent/schemas.py ReasoningStep.
export interface ReasoningStep {
    step: number;
    title: string;
    description: string;
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
