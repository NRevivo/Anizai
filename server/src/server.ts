import express from 'express';
import pinoHttp from 'pino-http';
import { logger } from './lib/logger.js';
import { env, isDev } from './config/env.js';
import { requestIdMiddleware } from './middleware/requestId.js';
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
    const allowedOrigins = new Set([
        'http://localhost:5173',
        'http://127.0.0.1:5173',
    ]);

    // ─────────────────────────────────────────────────────────────
    // Core Middleware
    // ─────────────────────────────────────────────────────────────

    // Request ID (must be first)
    app.use(requestIdMiddleware);
    app.disable('etag');

    // Request logging
    // @ts-expect-error - pino-http types are overly strict with logger generic
    app.use(pinoHttp({ logger }));

    // Body parsing
    app.use(express.json());
    app.use(express.urlencoded({ extended: true }));

    // CORS for local frontend development
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
