import type { Prediction, PredictionSession, SentimentDataPoint, TimelineEvent, SuggestedAction } from '../types';

export const mockSessions: PredictionSession[] = [
    {
        id: '1',
        question: 'Will AI regulation pass in the EU by Q2 2026?',
        probability: 0.723,
        status: 'done',
        lastUpdated: new Date('2026-01-06T10:30:00'),
    },
    {
        id: '2',
        question: 'Will Bitcoin reach $150k by end of 2026?',
        probability: 0.458,
        status: 'done',
        lastUpdated: new Date('2026-01-06T14:15:00'),
    },
    {
        id: '3',
        question: 'Will SpaceX successfully land on Mars in 2026?',
        probability: 0.124,
        status: 'done',
        lastUpdated: new Date('2026-01-05T18:45:00'),
    },
    {
        id: '4',
        question: 'Will global inflation drop below 2% in 2026?',
        probability: 0.389,
        status: 'done',
        lastUpdated: new Date('2026-01-06T09:20:00'),
    },
];

export const mockCurrentPrediction: Prediction = {
    id: '1',
    question: 'Will AI regulation pass in the EU by Q2 2026?',
    probability: 0.723,
    confidenceIndex: 0.84,
    status: 'done',
    explanation: 'Committee discussions have accelerated significantly. While industry lobbying poses a delay risk, the political cost of inaction has risen, creating a favorable environment for comprehensive regulation by mid-2026.',
    marketProbability: 0.685,
    createdAt: new Date('2026-01-04T12:00:00'),
    updatedAt: new Date('2026-01-06T10:30:00'),
};

export const mockSentimentData: SentimentDataPoint[] = [
    { date: 'Dec 15', expertSentiment: 0.65, expertUpper: 0.70, expertLower: 0.60, publicSentiment: 0.42 },
    { date: 'Dec 20', expertSentiment: 0.68, expertUpper: 0.74, expertLower: 0.62, publicSentiment: 0.48 },
    { date: 'Dec 25', expertSentiment: 0.70, expertUpper: 0.75, expertLower: 0.65, publicSentiment: 0.52 },
    { date: 'Dec 30', expertSentiment: 0.72, expertUpper: 0.78, expertLower: 0.66, publicSentiment: 0.55 },
    { date: 'Jan 4',  expertSentiment: 0.75, expertUpper: 0.82, expertLower: 0.68, publicSentiment: 0.58 },
    { date: 'Jan 6',  expertSentiment: 0.78, expertUpper: 0.85, expertLower: 0.71, publicSentiment: 0.62 },
];

export const mockTimelineEvents: TimelineEvent[] = [
    {
        id: '1',
        date: 'Jan 5',
        title: 'EU Parliament Committee Vote',
        sourceType: 'news',
        impactOnForecast: 'positive',
        impactLabel: 'Strong positive',
        isKeyEvidence: true,
        description: 'Key committee approved draft regulation with 18-4 vote, clearing major legislative hurdle.',
    },
    {
        id: '2',
        date: 'Jan 3',
        title: 'Expert Panel Consensus',
        sourceType: 'expert',
        impactOnForecast: 'positive',
        impactLabel: 'Moderate positive',
        isKeyEvidence: true,
        description: 'Leading policy experts predict Q2 passage at 75% likelihood given current momentum.',
    },
    {
        id: '3',
        date: 'Dec 28',
        title: 'Industry Pushback',
        sourceType: 'news',
        impactOnForecast: 'negative',
        impactLabel: 'Minor negative',
        description: 'Tech coalition lobbies for delay citing implementation concerns, but political will remains high.',
    },
    {
        id: '4',
        date: 'Dec 20',
        title: 'Public Support Grows',
        sourceType: 'social',
        impactOnForecast: 'positive',
        impactLabel: 'Contextual',
        description: 'Polling shows 68% public support for AI regulation, reducing political risk for lawmakers.',
    },
    {
        id: '5',
        date: 'Dec 15',
        title: 'Draft Text Leaked',
        sourceType: 'news',
        impactOnForecast: 'neutral',
        impactLabel: 'Neutral',
        description: 'Leaked draft confirms focus on high-risk AI, aligning with market expectations.',
    },
];

export const mockSuggestedActions: SuggestedAction[] = [
    { id: '1', label: 'Uncertainty drivers', icon: 'HelpCircle' },
    { id: '2', label: 'Similar historical events', icon: 'GitCompare' },
    { id: '3', label: 'Track this forecast', icon: 'Bell' },
];

export const mockChatMessages = [
    {
        id: '1',
        role: 'assistant' as const,
        content: 'The EU AI regulation has strong momentum. Recent committee votes show broad support, and the legislative timeline aligns with Q2 passage. However, industry lobbying and implementation complexity remain key uncertainties.',
        timestamp: new Date('2026-01-06T10:30:00'),
    },
    {
        id: '2',
        role: 'user' as const,
        content: 'What are the main factors driving this prediction?',
        timestamp: new Date('2026-01-06T10:31:00'),
    },
    {
        id: '3',
        role: 'assistant' as const,
        content: 'Three primary factors:\n\n1. **Legislative Momentum**: The draft has passed multiple committee stages with strong majorities\n2. **Expert Consensus**: Policy analysts cite 75%+ likelihood based on historical EU regulatory patterns\n3. **Political Will**: Cross-party support exists, reducing partisan blocking risk\n\nThe main downside risk is industry lobbying for delays, but this appears manageable given current political climate.',
        timestamp: new Date('2026-01-06T10:32:00'),
    },
];
