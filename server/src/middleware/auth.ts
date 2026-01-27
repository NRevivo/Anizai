import type { Request, Response, NextFunction } from 'express';
import { adminAuth } from '../lib/firebase.js';
import { logger } from '../lib/logger.js';
import { AppError } from './error.js';
import type { AuthUser } from '../types/api.js';

/**
 * Authentication middleware
 * Verifies Firebase ID token from Authorization header
 * Attaches user info to req.user
 */
export async function authMiddleware(
    req: Request,
    res: Response,
    next: NextFunction
): Promise<void> {
    try {
        const authHeader = req.headers.authorization;

        if (!authHeader || !authHeader.startsWith('Bearer ')) {
            throw new AppError('Missing or invalid Authorization header', 401, 'UNAUTHORIZED');
        }

        const token = authHeader.split('Bearer ')[1];

        if (!token) {
            throw new AppError('Missing token', 401, 'UNAUTHORIZED');
        }

        // Verify the ID token
        const decodedToken = await adminAuth.verifyIdToken(token);

        // Attach user to request
        const user: AuthUser = {
            uid: decodedToken.uid,
            email: decodedToken.email,
        };

        req.user = user;

        logger.debug({ uid: user.uid, requestId: req.requestId }, 'User authenticated');

        next();
    } catch (error) {
        if (error instanceof AppError) {
            next(error);
            return;
        }

        // Firebase auth errors
        logger.warn({ err: error, requestId: req.requestId }, 'Authentication failed');
        next(new AppError('Invalid or expired token', 401, 'UNAUTHORIZED'));
    }
}

/**
 * Require authentication - use after authMiddleware
 * Ensures req.user is defined
 */
export function requireAuth(req: Request, _res: Response, next: NextFunction): void {
    if (!req.user) {
        next(new AppError('Authentication required', 401, 'UNAUTHORIZED'));
        return;
    }
    next();
}
