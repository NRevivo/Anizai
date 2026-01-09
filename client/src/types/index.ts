// Core prediction types
export interface Prediction {
    id: string;
    question: string;
    probability: number; // 0-100
    confidenceIndex: number; // 0-100
    status: 'stable' | 'volatile';
    explanation: string;
    marketProbability?: number;
    createdAt: Date;
    updatedAt: Date;
}

export interface PredictionSession {
    id: string;
    question: string;
    probability: number;
    status: 'stable' | 'volatile';
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
    expertSentiment: number; // -100 to 100
    expertUpper?: number;
    expertLower?: number;
    publicSentiment: number; // -100 to 100
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
