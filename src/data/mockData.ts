import type { Prediction, PredictionSession, SentimentDataPoint, TimelineEvent, SuggestedAction } from '../types';

export const mockSessions: PredictionSession[] = [
    {
        id: '1',
        question: 'Will AI regulation pass in the EU by Q2 2026?',
        probability: 72.3,
        status: 'stable',
        lastUpdated: new Date('2026-01-06T10:30:00'),
    },
    {
        id: '2',
        question: 'Will Bitcoin reach $150k by end of 2026?',
        probability: 45.8,
        status: 'volatile',
        lastUpdated: new Date('2026-01-06T14:15:00'),
    },
    {
        id: '3',
        question: 'Will SpaceX successfully land on Mars in 2026?',
        probability: 12.4,
        status: 'stable',
        lastUpdated: new Date('2026-01-05T18:45:00'),
    },
    {
        id: '4',
        question: 'Will global inflation drop below 2% in 2026?',
        probability: 38.9,
        status: 'volatile',
        lastUpdated: new Date('2026-01-06T09:20:00'),
    },
];

export const mockCurrentPrediction: Prediction = {
    id: '1',
    question: 'Will AI regulation pass in the EU by Q2 2026?',
    probability: 72.3,
    confidenceIndex: 84,
    status: 'stable',
    explanation: 'Based on recent legislative momentum, expert consensus, and historical precedent with GDPR implementation, there is a strong likelihood of comprehensive AI regulation passing in the EU by Q2 2026. Key indicators include accelerated committee discussions and broad political support.',
    marketProbability: 68.5,
    createdAt: new Date('2026-01-04T12:00:00'),
    updatedAt: new Date('2026-01-06T10:30:00'),
};

export const mockSentimentData: SentimentDataPoint[] = [
    { date: 'Dec 15', expertSentiment: 65, publicSentiment: 42 },
    { date: 'Dec 20', expertSentiment: 68, publicSentiment: 48 },
    { date: 'Dec 25', expertSentiment: 70, publicSentiment: 52 },
    { date: 'Dec 30', expertSentiment: 72, publicSentiment: 55 },
    { date: 'Jan 4', expertSentiment: 75, publicSentiment: 58 },
    { date: 'Jan 6', expertSentiment: 78, publicSentiment: 62 },
];

export const mockTimelineEvents: TimelineEvent[] = [
    {
        id: '1',
        date: 'Jan 5',
        title: 'EU Parliament Committee Vote',
        sourceType: 'news',
        impact: 'positive',
        description: 'Key committee approved draft regulation with 18-4 vote',
    },
    {
        id: '2',
        date: 'Jan 3',
        title: 'Expert Panel Consensus',
        sourceType: 'expert',
        impact: 'positive',
        description: 'Leading policy experts predict Q2 passage at 75% likelihood',
    },
    {
        id: '3',
        date: 'Dec 28',
        title: 'Industry Pushback',
        sourceType: 'news',
        impact: 'negative',
        description: 'Tech coalition lobbies for delay citing implementation concerns',
    },
    {
        id: '4',
        date: 'Dec 20',
        title: 'Public Support Grows',
        sourceType: 'social',
        impact: 'positive',
        description: 'Polling shows 68% public support for AI regulation',
    },
];

export const mockSuggestedActions: SuggestedAction[] = [
    { id: '1', label: 'Explain uncertainty', icon: 'HelpCircle' },
    { id: '2', label: 'Compare to similar events', icon: 'GitCompare' },
    { id: '3', label: 'Track this event', icon: 'Bell' },
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
