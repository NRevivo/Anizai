import { Router } from 'express';
import { firestore } from '../lib/firebase.js';
import type { ApiSuccessResponse } from '../types/api.js';
import { AppError } from '../middleware/error.js';

const router = Router();

/**
 * GET /demo/sessions
 * List all sessions for demo user (for testing without auth)
 * REMOVE IN PRODUCTION
 */
router.get('/demo/sessions', async (_req, res, next) => {
    try {
        const DEMO_USER_ID = 'demo-user-001';

        const snapshot = await firestore
            .collection('sessions')
            .where('userId', '==', DEMO_USER_ID)
            .limit(50)
            .get();

        const sessions = snapshot.docs.map((doc) => ({
            id: doc.id,
            ...doc.data(),
            createdAt: doc.data().createdAt?.toDate?.()?.toISOString(),
            updatedAt: doc.data().updatedAt?.toDate?.()?.toISOString(),
            lastActivityAt: doc.data().lastActivityAt?.toDate?.()?.toISOString(),
        }));

        const response: ApiSuccessResponse = {
            data: sessions,
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * GET /demo/sessions/:id
 * Get session details for demo user (for testing without auth)
 * REMOVE IN PRODUCTION
 */
router.get('/demo/sessions/:id', async (req, res, next) => {
    try {
        const DEMO_USER_ID = 'demo-user-001';
        const sessionId = req.params.id;

        // Get session
        const sessionDoc = await firestore.collection('sessions').doc(sessionId).get();

        if (!sessionDoc.exists) {
            throw new AppError('Session not found', 404, 'NOT_FOUND');
        }

        const sessionData = sessionDoc.data()!;

        if (sessionData.userId !== DEMO_USER_ID) {
            throw new AppError('Session not found', 404, 'NOT_FOUND');
        }

        // Get messages (no orderBy to avoid index requirement)
        const messagesSnapshot = await firestore
            .collection('sessions')
            .doc(sessionId)
            .collection('messages')
            .get();

        const messages = messagesSnapshot.docs.map((doc) => ({
            id: doc.id,
            ...doc.data(),
            createdAt: doc.data().createdAt?.toDate?.()?.toISOString(),
        }));

        // Get prediction series
        const seriesSnapshot = await firestore
            .collection('sessions')
            .doc(sessionId)
            .collection('predictionSeries')
            .get();

        const predictionSeries = seriesSnapshot.docs.map((doc) => ({
            id: doc.id,
            ...doc.data(),
            ts: doc.data().ts?.toDate?.()?.toISOString(),
        }));

        // Get evidence
        const evidenceSnapshot = await firestore
            .collection('sessions')
            .doc(sessionId)
            .collection('evidence')
            .limit(20)
            .get();

        const evidence = evidenceSnapshot.docs.map((doc) => ({
            id: doc.id,
            ...doc.data(),
            createdAt: doc.data().createdAt?.toDate?.()?.toISOString(),
            publishedAt: doc.data().publishedAt?.toDate?.()?.toISOString(),
        }));

        // Get session result
        const resultDoc = await firestore.collection('sessionResults').doc(sessionId).get();
        const result = resultDoc.exists
            ? {
                ...resultDoc.data(),
                createdAt: resultDoc.data()?.createdAt?.toDate?.()?.toISOString(),
                updatedAt: resultDoc.data()?.updatedAt?.toDate?.()?.toISOString(),
            }
            : null;

        const response: ApiSuccessResponse = {
            data: {
                session: {
                    id: sessionDoc.id,
                    ...sessionData,
                    createdAt: sessionData.createdAt?.toDate?.()?.toISOString(),
                    updatedAt: sessionData.updatedAt?.toDate?.()?.toISOString(),
                    lastActivityAt: sessionData.lastActivityAt?.toDate?.()?.toISOString(),
                },
                messages,
                predictionSeries,
                evidence,
                result,
            },
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

/**
 * GET /demo/user
 * Get demo user profile (for testing)
 * REMOVE IN PRODUCTION
 */
router.get('/demo/user', async (_req, res, next) => {
    try {
        const DEMO_USER_ID = 'demo-user-001';

        const userDoc = await firestore.collection('users').doc(DEMO_USER_ID).get();

        if (!userDoc.exists) {
            throw new AppError('User not found', 404, 'NOT_FOUND');
        }

        const userData = userDoc.data()!;

        const response: ApiSuccessResponse = {
            data: {
                id: userDoc.id,
                ...userData,
                lastLoginAt: userData.lastLoginAt?.toDate?.()?.toISOString(),
                createdAt: userData.createdAt?.toDate?.()?.toISOString(),
                updatedAt: userData.updatedAt?.toDate?.()?.toISOString(),
            },
        };

        res.json(response);
    } catch (error) {
        next(error);
    }
});

export default router;
