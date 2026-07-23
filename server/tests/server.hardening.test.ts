import { describe, it, expect, beforeAll } from 'vitest';
import request from 'supertest';
import type { Application } from 'express';
import { createApp } from '../src/server.js';
import { validationError } from '../src/middleware/error.js';
import { z } from 'zod';

let app: Application;

beforeAll(async () => {
    app = await createApp();
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const agent = () => request(app as any);

describe('KG-C-10a — path-scoped auth', () => {
    it('rejects an unauthenticated GET /sessions with 401', async () => {
        const res = await agent().get('/sessions');
        expect(res.status).toBe(401);
        expect(res.body.error.code).toBe('UNAUTHORIZED');
    });

    it('rejects an unauthenticated GET /me with 401', async () => {
        const res = await agent().get('/me');
        expect(res.status).toBe(401);
        expect(res.body.error.code).toBe('UNAUTHORIZED');
    });

    it('rejects an unauthenticated nested /sessions/:id path with 401', async () => {
        const res = await agent().post('/sessions/abc/messages').send({});
        expect(res.status).toBe(401);
    });

    it('leaves public routes reachable without a token', async () => {
        expect((await agent().get('/health')).status).toBe(200);
        // /trending is public; it may 200 or 5xx depending on upstream, but must not 401.
        expect((await agent().get('/trending')).status).not.toBe(401);
    });

    it('does not swallow unknown routes into auth — they still 404', async () => {
        const res = await agent().get('/unknown-route');
        expect(res.status).toBe(404);
        expect(res.body.error.code).toBe('NOT_FOUND');
    });
});

describe('KG-C-10b — env-driven CORS', () => {
    it('echoes an allow-listed dev origin', async () => {
        const res = await agent().get('/health').set('Origin', 'http://localhost:5173');
        expect(res.headers['access-control-allow-origin']).toBe('http://localhost:5173');
        expect(res.headers['vary']).toContain('Origin');
    });

    it('does not echo an unlisted origin', async () => {
        const res = await agent().get('/health').set('Origin', 'https://evil.example.com');
        expect(res.headers['access-control-allow-origin']).toBeUndefined();
    });

    it('answers a preflight OPTIONS with 204', async () => {
        const res = await agent().options('/sessions').set('Origin', 'http://localhost:5173');
        expect(res.status).toBe(204);
    });
});

describe('KG-C-10d — validationError forwards field detail', () => {
    it('carries flattened field errors in details', () => {
        const schema = z.object({ plan: z.enum(['free', 'premium']) });
        const parsed = schema.safeParse({ plan: 'enterprise' });
        expect(parsed.success).toBe(false);

        const err = validationError((parsed as { error: z.ZodError }).error);
        expect(err.statusCode).toBe(400);
        expect(err.code).toBe('VALIDATION_ERROR');

        const details = err.details as { fieldErrors: Record<string, string[]> };
        expect(details.fieldErrors.plan).toBeDefined();
        expect(details.fieldErrors.plan.length).toBeGreaterThan(0);
    });
});
