import express from 'express';
import pinoHttp from 'pino-http';
import { logger } from './lib/logger.js';
import { isDev } from './config/env.js';
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

    // ─────────────────────────────────────────────────────────────
    // Core Middleware
    // ─────────────────────────────────────────────────────────────

    // Request ID (must be first)
    app.use(requestIdMiddleware);

    // Request logging
    // @ts-expect-error - pino-http types are overly strict with logger generic
    app.use(pinoHttp({ logger }));

    // Body parsing
    app.use(express.json());
    app.use(express.urlencoded({ extended: true }));

    // ─────────────────────────────────────────────────────────────
    // Public Routes
    // ─────────────────────────────────────────────────────────────

    app.use(rootRoutes);
    app.use(healthRoutes);
    app.use(trendingRoutes);

    // ─────────────────────────────────────────────────────────────
    // Demo Routes (dev only)
    // ─────────────────────────────────────────────────────────────

    if (isDev) {
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
