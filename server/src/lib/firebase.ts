import { initializeApp, applicationDefault, getApps } from 'firebase-admin/app';
import { getAuth } from 'firebase-admin/auth';
import { getFirestore } from 'firebase-admin/firestore';
import { env } from '../config/env.js';
import { logger } from './logger.js';

/**
 * Firebase Admin SDK initialization using Application Default Credentials (ADC)
 *
 * Local development:
 *   Run `gcloud auth application-default login` once per machine.
 *
 * Production (Cloud Run / GCE / Firebase):
 *   ADC automatically uses the attached service account.
 *
 * This project intentionally does NOT use service account key files.
 */

// Ensure Firebase Admin is initialized exactly once
if (getApps().length === 0) {
    initializeApp({
        credential: applicationDefault(),
        projectId: env.FIREBASE_PROJECT_ID,
    });

    logger.info({ projectId: env.FIREBASE_PROJECT_ID }, 'Firebase Admin SDK initialized with ADC');
}

/**
 * Firebase Auth Admin instance
 * Use for verifying ID tokens, managing users, etc.
 */
export const adminAuth = getAuth();

/**
 * Firestore instance
 * Use for database operations
 */
export const firestore = getFirestore();
