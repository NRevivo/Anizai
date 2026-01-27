import type { Request, Response, NextFunction } from 'express';
import { logger } from '../lib/logger.js';
import { isProd } from '../config/env.js';
import type { ApiErrorResponse } from '../types/api.js';

/**
 * Custom error class with status code and error code
 */
export class AppError extends Error {
    constructor(
        message: string,
        public statusCode: number = 500,
        public code?: string
    ) {
        super(message);
        this.name = 'AppError';
    }
}

/**
 * Centralized error handling middleware.
 * - Returns structured JSON error response
 * - Hides stack traces in production
 * - Logs errors with request context
 */
export function errorMiddleware(
    err: Error | AppError,
    req: Request,
    res: Response,
    _next: NextFunction
): void {
    const statusCode = err instanceof AppError ? err.statusCode : 500;
    const code = err instanceof AppError ? err.code : 'INTERNAL_ERROR';

    // Log the error
    logger.error(
        {
            err: isProd ? { message: err.message } : err,
            requestId: req.requestId,
            method: req.method,
            path: req.path,
        },
        'Request error'
    );

    const response: ApiErrorResponse = {
        error: {
            message: isProd && statusCode === 500 ? 'Internal server error' : err.message,
            code,
        },
        meta: {
            requestId: req.requestId,
            timestamp: new Date().toISOString(),
        },
    };

    res.status(statusCode).json(response);
}

/**
 * 404 Not Found handler
 */
export function notFoundMiddleware(req: Request, res: Response): void {
    const response: ApiErrorResponse = {
        error: {
            message: `Route not found: ${req.method} ${req.path}`,
            code: 'NOT_FOUND',
        },
        meta: {
            requestId: req.requestId,
            timestamp: new Date().toISOString(),
        },
    };

    res.status(404).json(response);
}
