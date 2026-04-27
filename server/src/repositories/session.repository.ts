import type {
    Session, SessionMessage, PredictionPoint,
    Evidence, SentimentDataPoint, SessionResult, ClarificationCandidate,
    CreateSessionInput, CreateMessageInput
} from '../services/sessions.service.js';
import { batch, collectionRef, now, toISOString } from '../services/firebase.service.js';
import { randomUUID } from 'node:crypto';

function isFailedPrecondition(error: unknown): boolean {
    if (!error || typeof error !== 'object') {
        return false;
    }
    const maybeError = error as { code?: string | number };
    return maybeError.code === 9 || maybeError.code === 'failed-precondition';
}

function mapSessionDoc(doc: FirebaseFirestore.QueryDocumentSnapshot): Session {
    const data = doc.data();
    return {
        id: doc.id,
        userId: data.userId,
        question: data.question,
        title: data.title ?? null,
        status: data.status,
        latestProbability: data.latestProbability ?? null,
        latestConfidence: data.latestConfidence ?? null,
        followEnabled: data.followEnabled ?? false,
        isFollowing: data.isFollowing ?? false,
        canonicalKey: data.canonicalKey ?? null,
        errorCode: data.errorCode ?? null,
        errorMessage: data.errorMessage ?? null,
        clarificationCandidates: (data.clarificationCandidates ?? null) as ClarificationCandidate[] | null,
        createdAt: toISOString(data.createdAt) ?? '',
        updatedAt: toISOString(data.updatedAt) ?? '',
        lastActivityAt: toISOString(data.lastActivityAt) ?? '',
    };
}

function byDateAsc<T extends { createdAt?: string; ts?: string }>(a: T, b: T): number {
    const aDate = new Date(a.ts ?? a.createdAt ?? '').getTime();
    const bDate = new Date(b.ts ?? b.createdAt ?? '').getTime();
    return aDate - bDate;
}

function byDateDesc<T extends { createdAt?: string }>(a: T, b: T): number {
    const aDate = new Date(a.createdAt ?? '').getTime();
    const bDate = new Date(b.createdAt ?? '').getTime();
    return bDate - aDate;
}

async function deleteSubcollectionDocs(
    collection: FirebaseFirestore.CollectionReference,
    chunkSize = 200
): Promise<void> {
    while (true) {
        const snapshot = await collection.limit(chunkSize).get();

        if (snapshot.empty) {
            break;
        }

        const writeBatch = batch();
        snapshot.docs.forEach((doc) => {
            writeBatch.delete(doc.ref);
        });
        await writeBatch.commit();

        if (snapshot.size < chunkSize) {
            break;
        }
    }
}

export const sessionRepository = {
    async listSessions(userId: string, limit = 50): Promise<Session[]> {
        try {
            const snapshot = await collectionRef('sessions')
                .where('userId', '==', userId)
                .orderBy('lastActivityAt', 'desc')
                .limit(limit)
                .get();

            return snapshot.docs.map(mapSessionDoc);
        } catch (error) {
            // Local/dev fallback when composite index is not deployed yet.
            if (!isFailedPrecondition(error)) {
                throw error;
            }

            const snapshot = await collectionRef('sessions')
                .where('userId', '==', userId)
                .get();

            return snapshot.docs
                .map(mapSessionDoc)
                .sort((a, b) => new Date(b.lastActivityAt).getTime() - new Date(a.lastActivityAt).getTime())
                .slice(0, limit);
        }
    },

    async getSession(sessionId: string): Promise<Session | null> {
        const doc = await collectionRef('sessions').doc(sessionId).get();

        if (!doc.exists) {
            return null;
        }

        const data = doc.data()!;

        return {
            id: doc.id,
            userId: data.userId,
            question: data.question,
            title: data.title ?? null,
            status: data.status,
            latestProbability: data.latestProbability ?? null,
            latestConfidence: data.latestConfidence ?? null,
            followEnabled: data.followEnabled ?? false,
            isFollowing: data.isFollowing ?? false,
            canonicalKey: data.canonicalKey ?? null,
            errorCode: data.errorCode ?? null,
            errorMessage: data.errorMessage ?? null,
            clarificationCandidates: (data.clarificationCandidates ?? null) as ClarificationCandidate[] | null,
            createdAt: toISOString(data.createdAt) ?? '',
            updatedAt: toISOString(data.updatedAt) ?? '',
            lastActivityAt: toISOString(data.lastActivityAt) ?? '',
        };
    },

    async getSessionResult(sessionId: string): Promise<SessionResult | null> {
        const resultDoc = await collectionRef('sessionResults').doc(sessionId).get();

        if (!resultDoc.exists) {
            return null;
        }

        const data = resultDoc.data()!;

        return {
            sessionId: resultDoc.id,
            userId: data.userId,
            finalProbability: data.finalProbability,
            confidence: data.confidence,
            marketComparison: data.marketComparison ?? [],
            summaryMarkdown: data.summaryMarkdown,
            createdAt: toISOString(data.createdAt) ?? '',
            updatedAt: toISOString(data.updatedAt) ?? '',
            confidenceLabel: data.confidenceLabel ?? null,
            consensusStrength: data.consensusStrength ?? null,
            evidenceVolumeLabel: data.evidenceVolumeLabel ?? null,
            bottomLineAnswer: data.bottomLineAnswer ?? null,
            detailedExplanation: data.detailedExplanation ?? null,
            marketProbability: data.marketProbability ?? null,
            marketComparisonInsight: data.marketComparisonInsight ?? null,
            sentimentAnalysisInsight: data.sentimentAnalysisInsight ?? null,
            evidenceFeedSummary: data.evidenceFeedSummary ?? null,
        };
    },

    async getMessages(sessionId: string): Promise<SessionMessage[]> {
        const mapMessageDoc = (doc: FirebaseFirestore.QueryDocumentSnapshot): SessionMessage => {
            const data = doc.data();
            return {
                id: doc.id,
                role: data.role,
                content: data.content,
                createdAt: toISOString(data.createdAt) ?? '',
                status: data.status ?? null,
                meta: data.meta ?? null,
            };
        };

        try {
            const messagesSnapshot = await collectionRef('sessions')
                .doc(sessionId)
                .collection('messages')
                .orderBy('createdAt', 'asc')
                .get();

            return messagesSnapshot.docs.map(mapMessageDoc);
        } catch (error) {
            if (!isFailedPrecondition(error)) {
                throw error;
            }
            const messagesSnapshot = await collectionRef('sessions')
                .doc(sessionId)
                .collection('messages')
                .get();

            return messagesSnapshot.docs.map(mapMessageDoc).sort(byDateAsc);
        }
    },

    async getPredictionSeries(sessionId: string): Promise<PredictionPoint[]> {
        const mapSeriesDoc = (doc: FirebaseFirestore.QueryDocumentSnapshot): PredictionPoint => {
            const data = doc.data();
            return {
                id: doc.id,
                ts: toISOString(data.ts) ?? '',
                probability: data.probability,
                confidence: data.confidence,
                reasonType: data.reasonType,
                evidenceIds: data.evidenceIds ?? [],
            };
        };

        try {
            const seriesSnapshot = await collectionRef('sessions')
                .doc(sessionId)
                .collection('predictionSeries')
                .orderBy('ts', 'asc')
                .get();

            return seriesSnapshot.docs.map(mapSeriesDoc);
        } catch (error) {
            if (!isFailedPrecondition(error)) {
                throw error;
            }
            const seriesSnapshot = await collectionRef('sessions')
                .doc(sessionId)
                .collection('predictionSeries')
                .get();

            return seriesSnapshot.docs.map(mapSeriesDoc).sort(byDateAsc);
        }
    },

    async getEvidence(sessionId: string, limit = 50): Promise<Evidence[]> {
        const mapEvidenceDoc = (doc: FirebaseFirestore.QueryDocumentSnapshot): Evidence => {
            const data = doc.data();
            return {
                id: doc.id,
                type: data.type,
                title: data.title,
                snippet: data.snippet,
                url: data.url ?? null,
                publishedAt: toISOString(data.publishedAt),
                sourceId: data.sourceId ?? null,
                score: data.score,
                createdAt: toISOString(data.createdAt) ?? '',
                impact: data.impact ?? null,
                impactLabel: data.impactLabel ?? null,
                isKeyEvidence: data.isKeyEvidence ?? false,
            };
        };

        try {
            const evidenceSnapshot = await collectionRef('sessions')
                .doc(sessionId)
                .collection('evidence')
                .orderBy('createdAt', 'desc')
                .limit(limit)
                .get();

            return evidenceSnapshot.docs.map(mapEvidenceDoc);
        } catch (error) {
            if (!isFailedPrecondition(error)) {
                throw error;
            }
            const evidenceSnapshot = await collectionRef('sessions')
                .doc(sessionId)
                .collection('evidence')
                .get();

            return evidenceSnapshot.docs
                .map(mapEvidenceDoc)
                .sort(byDateDesc)
                .slice(0, limit);
        }
    },

    async getSentimentTimeSeries(sessionId: string, limit = 100): Promise<SentimentDataPoint[]> {
        const mapSentimentDoc = (doc: FirebaseFirestore.QueryDocumentSnapshot): SentimentDataPoint => {
            const data = doc.data();
            return {
                id: doc.id,
                ts: toISOString(data.ts) ?? '',
                date: data.date ?? '',
                expertSentiment: data.expertSentiment ?? 0,
                expertUpper: data.expertUpper ?? null,
                expertLower: data.expertLower ?? null,
                publicSentiment: data.publicSentiment ?? 0,
                createdAt: toISOString(data.createdAt) ?? '',
            };
        };

        try {
            const sentimentSnapshot = await collectionRef('sessions')
                .doc(sessionId)
                .collection('sentimentTimeSeries')
                .orderBy('ts', 'asc')
                .limit(limit)
                .get();

            return sentimentSnapshot.docs.map(mapSentimentDoc);
        } catch (error) {
            if (!isFailedPrecondition(error)) {
                throw error;
            }
            const sentimentSnapshot = await collectionRef('sessions')
                .doc(sessionId)
                .collection('sentimentTimeSeries')
                .get();

            return sentimentSnapshot.docs
                .map(mapSentimentDoc)
                .sort(byDateAsc)
                .slice(0, limit);
        }
    },

    async createSession(userId: string, input: CreateSessionInput): Promise<Session> {
        const createdAt = now();
        const sessionRef = collectionRef('sessions').doc();
        const writeBatch = batch();

        const sessionData = {
            userId,
            question: input.question,
            title: input.title ?? null,
            status: 'queued' as const,
            latestProbability: null,
            latestConfidence: null,
            followEnabled: false,
            isFollowing: false,
            canonicalKey: null,
            errorCode: null,
            errorMessage: null,
            clarificationCandidates: null,
            createdAt,
            updatedAt: createdAt,
            lastActivityAt: createdAt,
        };

        const forecastQueryData = {
            queryId: randomUUID(),
            sessionId: sessionRef.id,
            userId,
            question: input.question,
            status: 'pending' as const,
            createdAt,
            claimedAt: null,
            claimedBy: null,
        };

        writeBatch.set(sessionRef, sessionData);
        writeBatch.set(collectionRef('forecastQueries').doc(sessionRef.id), forecastQueryData);

        await writeBatch.commit();

        return {
            id: sessionRef.id,
            ...sessionData,
            createdAt: createdAt.toDate().toISOString(),
            updatedAt: createdAt.toDate().toISOString(),
            lastActivityAt: createdAt.toDate().toISOString(),
        };
    },

    async addMessage(sessionId: string, input: CreateMessageInput): Promise<SessionMessage> {
        const createdAt = now();

        const messageData = {
            role: input.role,
            content: input.content,
            createdAt,
            status: 'sent' as const,
            meta: input.meta ?? null,
        };

        const sessionRef = collectionRef('sessions').doc(sessionId);
        const writeBatch = batch();
        const messageRef = sessionRef.collection('messages').doc();

        writeBatch.set(messageRef, messageData);
        writeBatch.update(sessionRef, {
            lastActivityAt: createdAt,
            updatedAt: createdAt,
        });

        await writeBatch.commit();

        return {
            id: messageRef.id,
            ...messageData,
            createdAt: createdAt.toDate().toISOString(),
        };
    },

    async deleteSession(sessionId: string): Promise<void> {
        const sessionRef = collectionRef('sessions').doc(sessionId);

        const subcollections = [
            'messages',
            'predictionSeries',
            'evidence',
            'sentimentTimeSeries',
        ] as const;

        await Promise.all(
            subcollections.map((name) => deleteSubcollectionDocs(sessionRef.collection(name)))
        );

        const writeBatch = batch();
        writeBatch.delete(sessionRef);
        writeBatch.delete(collectionRef('sessionResults').doc(sessionId));
        writeBatch.delete(collectionRef('forecastQueries').doc(sessionId));
        await writeBatch.commit();
    },
};
