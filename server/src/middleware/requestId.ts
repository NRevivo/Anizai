import type { Request, Response, NextFunction } from 'express';
import crypto from 'crypto';

/**
 * Middleware that generates a unique request ID for each request.
 * - Attaches it to req.requestId
 * - Sets x-request-id response header
 */
export function requestIdMiddleware(req: Request, res: Response, next: NextFunction): void {
    const requestId = crypto.randomUUID();
    req.requestId = requestId;
    res.setHeader('x-request-id', requestId);
    next();
}
