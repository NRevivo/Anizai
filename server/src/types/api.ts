/**
 * Standard API response types
 */

export interface ApiSuccessResponse<T = unknown> {
    data: T;
    meta?: {
        requestId?: string;
        timestamp?: string;
    };
}

export interface ApiErrorResponse {
    error: {
        message: string;
        code?: string;
        details?: unknown;
    };
    meta?: {
        requestId?: string;
        timestamp?: string;
    };
}

export interface HealthResponse {
    ok: boolean;
    timestamp: string;
}

/**
 * Authenticated user info from Firebase token
 */
export interface AuthUser {
    uid: string;
    email: string | undefined;
}

/**
 * Extend Express Request
 */
declare global {
    // eslint-disable-next-line @typescript-eslint/no-namespace
    namespace Express {
        interface Request {
            requestId: string;
            user?: AuthUser;
        }
    }
}
