import { describe, it, expect, beforeAll } from 'vitest';
import request from 'supertest';
import type { Express } from 'express';
import { createApp } from '../src/server.js';

let app: Express.Application;

beforeAll(async () => {
    app = await createApp();
});

describe('GET /health', () => {
    it('should return ok: true with timestamp', async () => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const res = await request(app as any).get('/health');

        expect(res.status).toBe(200);
        expect(res.body.data).toBeDefined();
        expect(res.body.data.ok).toBe(true);
        expect(res.body.data.timestamp).toBeDefined();
        expect(new Date(res.body.data.timestamp).toISOString()).toBe(res.body.data.timestamp);
    });

    it('should include x-request-id header', async () => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const res = await request(app as any).get('/health');

        expect(res.headers['x-request-id']).toBeDefined();
        expect(res.headers['x-request-id']).toMatch(
            /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
        );
    });
});

describe('404 handling', () => {
    it('should return 404 for unknown routes', async () => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const res = await request(app as any).get('/unknown-route');

        expect(res.status).toBe(404);
        expect(res.body.error).toBeDefined();
        expect(res.body.error.code).toBe('NOT_FOUND');
    });
});
