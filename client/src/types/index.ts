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
