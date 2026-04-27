import { auth } from './firebase';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:3000';

interface ApiSuccessResponse<T> {
    data: T;
}

interface ApiErrorResponse {
    error?: {
        message?: string;
        code?: string;
    };
}

function getErrorPayload(body: ApiSuccessResponse<unknown> | ApiErrorResponse | null): ApiErrorResponse['error'] {
    if (!body || typeof body !== 'object' || !('error' in body)) {
        return undefined;
    }
    return body.error;
}

export class ApiError extends Error {
    status: number;
    code?: string;

    constructor(message: string, status: number, code?: string) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.code = code;
    }
}

export class ApiAuthError extends ApiError {
    constructor(message: string, status = 401, code?: string) {
        super(message, status, code);
        this.name = 'ApiAuthError';
    }
}

type RequestMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

interface ApiRequestOptions {
    method?: RequestMethod;
    body?: unknown;
    requireAuth?: boolean;
    signal?: AbortSignal;
}

async function getAuthToken(): Promise<string> {
    const user = auth.currentUser;
    if (!user) {
        throw new ApiAuthError('You must be signed in to continue.');
    }
    return user.getIdToken();
}

async function buildHeaders(requireAuth: boolean): Promise<HeadersInit> {
    const headers: Record<string, string> = {
        Accept: 'application/json',
        'Content-Type': 'application/json',
    };

    if (requireAuth) {
        const token = await getAuthToken();
        headers.Authorization = `Bearer ${token}`;
    }

    return headers;
}

export async function apiRequest<T>(
    path: string,
    options: ApiRequestOptions = {}
): Promise<T> {
    const {
        method = 'GET',
        body,
        requireAuth = true,
        signal,
    } = options;

    const response = await fetch(`${API_BASE_URL}${path}`, {
        method,
        headers: await buildHeaders(requireAuth),
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
        cache: 'no-store',
    });

    const parsedBody = (await response.json().catch(() => null)) as
        | ApiSuccessResponse<T>
        | ApiErrorResponse
        | null;

    if (!response.ok) {
        const errorPayload = getErrorPayload(parsedBody);
        const message =
            errorPayload?.message ??
            `Request failed: ${response.status} ${response.statusText}`;
        const code = errorPayload?.code;

        if (response.status === 401 || response.status === 403) {
            throw new ApiAuthError(message, response.status, code);
        }

        throw new ApiError(message, response.status, code);
    }

    return (parsedBody as ApiSuccessResponse<T>).data;
}
