import express from 'express';
import compression from 'compression';
import pinoHttp from 'pino-http';
import { logger } from './lib/logger.js';
import { env, isDev, isProd } from './config/env.js';
import { requestIdMiddleware } from './middleware/requestId.js';
import { authMiddleware } from './middleware/auth.js';
import { errorMiddleware, notFoundMiddleware } from './middleware/error.js';
import rootRoutes from './routes/root.js';
import healthRoutes from './routes/health.js';
import meRoutes from './routes/me.js';
import sessionsRoutes from './routes/sessions.js';
import trendingRoutes from './routes/trending.js';

/**
 * Create and configure Express application
 */
export async function createApp() {
    const app = express();

    // CORS allowlist. Vite dev origins are permitted only outside production;
    // deployed origins come from CORS_ORIGINS. In production nothing is allowed
    // unless it is explicitly configured — no hardcoded localhost (KG-C-10b).
    const devOrigins = isProd ? [] : ['http://localhost:5173', 'http://127.0.0.1:5173'];
    const allowedOrigins = new Set([...devOrigins, ...env.CORS_ORIGINS]);

    // ─────────────────────────────────────────────────────────────
    // Core Middleware
    // ─────────────────────────────────────────────────────────────

    // Request ID (must be first)
    app.use(requestIdMiddleware);
    app.disable('etag');

    // gzip/deflate every response above the default 1 KB threshold. Added
    // 2026-07-27: nothing compressed responses before, so `GET /trending` — a
    // public endpoint the landing page hits unauthenticated on every visit —
    // was crossing the wire uncompressed. Measured on that route: 3.6x smaller.
    // Must sit ahead of the routes so it can wrap their `res.json`.
    app.use(compression());

    // Request logging
    // @ts-expect-error - pino-http types are overly strict with logger generic
    app.use(pinoHttp({ logger }));

    // Body parsing
    app.use(express.json());
    app.use(express.urlencoded({ extended: true }));

    // CORS — echo only allow-listed origins (see allowedOrigins above)
    app.use((req, res, next) => {
        const origin = req.headers.origin;
        if (origin && allowedOrigins.has(origin)) {
            res.header('Access-Control-Allow-Origin', origin);
            res.header('Vary', 'Origin');
        }

        res.header('Access-Control-Allow-Methods', 'GET,POST,PUT,PATCH,DELETE,OPTIONS');
        res.header('Access-Control-Allow-Headers', 'Authorization,Content-Type');

        if (req.method === 'OPTIONS') {
            res.sendStatus(204);
            return;
        }

        next();
    });

    // ─────────────────────────────────────────────────────────────
    // Public Routes
    // ─────────────────────────────────────────────────────────────

    app.use(rootRoutes);
    app.use(healthRoutes);
    app.use(trendingRoutes);

    // ─────────────────────────────────────────────────────────────
    // Demo Routes (dev only)
    // ─────────────────────────────────────────────────────────────

    if (isDev && env.ALLOW_DEMO_ROUTES) {
        const demoRoutes = await import('./routes/demo.js');
        app.use(demoRoutes.default);
        logger.info('Demo routes enabled (development mode)');
    }

    // ─────────────────────────────────────────────────────────────
    // Protected Routes
    // ─────────────────────────────────────────────────────────────

    // Auth is gated by path prefix at the app level, so every route under
    // `/me` or `/sessions` is protected by construction — adding a handler to
    // either router cannot ship it public (KG-C-10a). Unmatched paths fall
    // through to the 404 handler without hitting auth.
    app.use(['/me', '/sessions'], authMiddleware);
    app.use(meRoutes);
    app.use(sessionsRoutes);

    // ─────────────────────────────────────────────────────────────
    // Error Handling
    // ─────────────────────────────────────────────────────────────

    // 404 handler
    app.use(notFoundMiddleware);

    // Global error handler
    app.use(errorMiddleware);

    return app;
}
