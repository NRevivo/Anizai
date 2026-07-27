import type {
    Session, SessionMessage, PredictionPoint,
    Evidence, SentimentDataPoint, SessionResult, ClarificationCandidate,
    CreateSessionInput, CreateMessageInput
} from '../services/sessions.service.js';
import { batch, collectionRef, now, serverTimestamp, toISOString } from '../services/firebase.service.js';
import { Timestamp } from 'firebase-admin/firestore';
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

// The data-pipeline agent writes finalProbability into sessionResults/{id}
// but never copies it onto the sessions doc, so sessions/{id}.latestProbability
// stays null. For done sessions missing it, derive from the result doc so the
// sidebar can show a probability. A directly-emitted value always wins, so this
// is a no-op once the pipeline starts writing latestProbability itself.
async function enrichLatestProbability(sessions: Session[]): Promise<Session[]> {
    const needsEnrichment = sessions.filter(
        (session) => session.status === 'done' && session.latestProbability === null
    );

    if (needsEnrichment.length === 0) {
        return sessions;
    }

    const derived = await Promise.all(
        needsEnrichment.map(async (session) => {
            const resultDoc = await collectionRef('sessionResults').doc(session.id).get();
            const finalProbability = resultDoc.exists ? resultDoc.data()?.finalProbability : undefined;
            return [
                session.id,
                typeof finalProbability === 'number' ? finalProbability : null,
            ] as const;
        })
    );
    const probabilityById = new Map(derived);

    return sessions.map((session) =>
        probabilityById.has(session.id)
            ? { ...session, latestProbability: probabilityById.get(session.id)! }
            : session
    );
}

function byDateAsc<T extends { createdAt?: string; ts?: string }>(a: T, b: T): number {
    const aDate = new Date(a.ts ?? a.createdAt ?? '').getTime();
    const bDate = new Date(b.ts ?? b.createdAt ?? '').getTime();
    return aDate - bDate;
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
    async findRecentSessionByIdempotencyKey(
        userId: string,
        idempotencyKey: string,
        windowMs = 60_000
    ): Promise<Session | null> {
        const cutoff = Timestamp.fromDate(new Date(Date.now() - windowMs));

        try {
            const snapshot = await collectionRef('sessions')
                .where('userId', '==', userId)
                .where('idempotencyKey', '==', idempotencyKey)
                .where('createdAt', '>=', cutoff)
                .orderBy('createdAt', 'desc')
                .limit(1)
                .get();

            return snapshot.empty ? null : mapSessionDoc(snapshot.docs[0]);
        } catch (error) {
            if (!isFailedPrecondition(error)) {
                throw error;
            }

            const snapshot = await collectionRef('sessions')
                .where('userId', '==', userId)
                .where('idempotencyKey', '==', idempotencyKey)
                .get();

            const matchingDoc = snapshot.docs
                .map(mapSessionDoc)
                .filter((session) => {
                    const createdAtMs = new Date(session.createdAt).getTime();
                    return Number.isFinite(createdAtMs) && createdAtMs >= cutoff.toDate().getTime();
                })
                .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())[0];

            return matchingDoc ?? null;
        }
    },

    async listSessions(userId: string, limit = 50): Promise<Session[]> {
        try {
            const snapshot = await collectionRef('sessions')
                .where('userId', '==', userId)
                .orderBy('lastActivityAt', 'desc')
                .limit(limit)
                .get();

            return enrichLatestProbability(snapshot.docs.map(mapSessionDoc));
        } catch (error) {
            // Local/dev fallback when composite index is not deployed yet.
            if (!isFailedPrecondition(error)) {
                throw error;
            }

            const snapshot = await collectionRef('sessions')
                .where('userId', '==', userId)
                .get();

            return enrichLatestProbability(
                snapshot.docs
                    .map(mapSessionDoc)
                    .sort((a, b) => new Date(b.lastActivityAt).getTime() - new Date(a.lastActivityAt).getTime())
                    .slice(0, limit)
            );
        }
    },

    async getSession(sessionId: string): Promise<Session | null> {
        const doc = await collectionRef('sessions').doc(sessionId).get();

        if (!doc.exists) {
            return null;
        }

        const data = doc.data()!;

        const session: Session = {
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

        const [enriched] = await enrichLatestProbability([session]);
        return enriched;
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
            summaryMarkdown: data.summaryMarkdown ?? null,
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
            keyFactors: data.keyFactors ?? [],
            whatIDidntFind: data.whatIDidntFind ?? [],
            reasoningChain: data.reasoningChain ?? [],
            suggestedActions: data.suggestedActions ?? [],
            generatedAt: toISOString(data.generatedAt),
            agentVersion: data.agentVersion ?? null,
            tier: data.tier ?? null,
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
                userId: data.userId ?? null,
                replyToMessageId: data.replyToMessageId ?? null,
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
        // Pipeline (data-pipeline/agent/schemas.py) writes snake_case keys.
        // Older fixtures may have camelCase; read both so we don't silently
        // null out fields that exist on disk.
        const mapEvidenceDoc = (doc: FirebaseFirestore.QueryDocumentSnapshot): Evidence => {
            const data = doc.data();
            return {
                id: doc.id,
                type: data.type,
                evidenceId: data.evidence_id ?? data.evidenceId ?? null,
                sourceType: data.source_type ?? data.sourceType ?? null,
                origin: data.origin ?? null,
                title: data.title,
                snippet: data.snippet,
                url: data.url ?? null,
                source: data.source ?? null,
                sourceDomain: data.source_domain ?? data.sourceDomain ?? null,
                publishedAt: toISOString(data.published_at ?? data.publishedAt),
                fetchedAt: toISOString(data.fetched_at ?? data.fetchedAt),
                sourceId: data.source_id ?? data.sourceId ?? null,
                score: data.score,
                relevanceScore: data.relevance_score ?? data.relevanceScore ?? null,
                credibilityTier: data.credibility_tier ?? data.credibilityTier ?? null,
                recencyWeight: data.recency_weight ?? data.recencyWeight ?? null,
                usedInAnswer: data.used_in_answer ?? data.usedInAnswer ?? null,
                impactOnForecast: data.impact_on_forecast ?? data.impactOnForecast ?? null,
                justification: data.justification ?? null,
                rank: data.rank ?? null,
                createdAt: toISOString(data.created_at ?? data.createdAt) ?? '',
                impact: data.impact ?? null,
                impactLabel: data.impact_label ?? data.impactLabel ?? null,
                isKeyEvidence: data.is_key_evidence ?? data.isKeyEvidence ?? false,
            };
        };

        // Don't .orderBy() here: the pipeline (write_evidence_batch) does
        // not write a `createdAt` field on evidence docs, so a server-side
        // orderBy would silently exclude every pipeline-written row.
        // Per-session evidence is small (~30–50 docs), so in-memory sort
        // is fine.
        const evidenceSnapshot = await collectionRef('sessions')
            .doc(sessionId)
            .collection('evidence')
            .get();

        return evidenceSnapshot.docs
            .map(mapEvidenceDoc)
            .sort((a, b) => {
                const aTime = new Date(a.publishedAt ?? a.createdAt ?? '').getTime();
                const bTime = new Date(b.publishedAt ?? b.createdAt ?? '').getTime();
                if (Number.isNaN(aTime) && Number.isNaN(bTime)) return 0;
                if (Number.isNaN(aTime)) return 1;
                if (Number.isNaN(bTime)) return -1;
                return bTime - aTime;
            })
            .slice(0, limit);
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
            idempotencyKey: input.idempotencyKey,
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

    async requeueClarifiedSession(
        session: Session,
        selectedCandidate: ClarificationCandidate | null
    ): Promise<Session> {
        const updatedAt = now();
        const sessionRef = collectionRef('sessions').doc(session.id);
        const forecastQueryRef = collectionRef('forecastQueries').doc();
        const writeBatch = batch();

        const sessionUpdate = {
            status: 'queued' as const,
            canonicalKey: selectedCandidate?.id ?? session.canonicalKey ?? null,
            errorCode: null,
            errorMessage: null,
            clarificationCandidates: null,
            updatedAt,
            lastActivityAt: updatedAt,
        };

        const forecastQueryData = {
            queryId: randomUUID(),
            sessionId: session.id,
            userId: session.userId,
            question: session.question,
            status: 'pending' as const,
            createdAt: updatedAt,
            claimedAt: null,
            claimedBy: null,
        };

        writeBatch.update(sessionRef, sessionUpdate);
        writeBatch.set(forecastQueryRef, forecastQueryData);

        await writeBatch.commit();

        return {
            ...session,
            ...sessionUpdate,
            updatedAt: updatedAt.toDate().toISOString(),
            lastActivityAt: updatedAt.toDate().toISOString(),
        };
    },

    // `createdAt` is written with Firestore's commit-time clock, NOT this host's
    // wall clock. The `messages` subcollection has two writers — this BFF for
    // user messages, and the data-pipeline agent for assistant replies, which
    // uses `firestore.SERVER_TIMESTAMP` (agent/firestore_client.py). Both are
    // ordered by `createdAt`, so writing `Timestamp.now()` here put two
    // independent clocks into one sort key: whenever this host's clock ran ahead
    // of Firestore's by more than the answer latency, the reply received a lower
    // `createdAt` than the question it answered and sorted above it.
    async addMessage(sessionId: string, userId: string, input: CreateMessageInput): Promise<SessionMessage> {
        const messageData = {
            userId,
            role: input.role,
            content: input.content,
            createdAt: serverTimestamp(),
            status: 'sent' as const,
            meta: input.meta ?? null,
        };

        const sessionRef = collectionRef('sessions').doc(sessionId);
        const writeBatch = batch();
        const messageRef = sessionRef.collection('messages').doc();

        writeBatch.set(messageRef, messageData);
        writeBatch.update(sessionRef, {
            lastActivityAt: serverTimestamp(),
            updatedAt: serverTimestamp(),
        });

        await writeBatch.commit();

        // The sentinel carries no readable value, so the resolved timestamp is
        // read back for the response. The client sorts the optimistic message by
        // this value, so it has to be the real committed one — an empty or
        // host-clock string would reintroduce the ordering bug in the reply.
        const writtenMessage = await messageRef.get();

        return {
            id: messageRef.id,
            userId,
            role: input.role,
            content: input.content,
            status: 'sent' as const,
            meta: input.meta ?? null,
            createdAt: toISOString(writtenMessage.get('createdAt')) ?? new Date().toISOString(),
        };
    },

    async deleteSession(sessionId: string): Promise<void> {
        const sessionRef = collectionRef('sessions').doc(sessionId);

        const subcollections = [
            'messages',
            'predictionSeries',
            'evidence',
            'sentimentTimeSeries',
            'agentEvents',
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
