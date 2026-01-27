import { createApp } from './server.js';
import { env } from './config/env.js';
import { logger } from './lib/logger.js';

async function main() {
    const app = await createApp();

    const server = app.listen(env.PORT, () => {
        logger.info(
            {
                port: env.PORT,
                env: env.NODE_ENV,
            },
            `🚀 Server started on http://localhost:${env.PORT}`
        );
    });

    // Graceful shutdown
    const shutdown = (signal: string) => {
        logger.info(`${signal} received, shutting down gracefully...`);
        server.close(() => {
            logger.info('Server closed');
            process.exit(0);
        });

        // Force exit after 10 seconds
        setTimeout(() => {
            logger.error('Forcing exit after timeout');
            process.exit(1);
        }, 10000);
    };

    process.on('SIGTERM', () => shutdown('SIGTERM'));
    process.on('SIGINT', () => shutdown('SIGINT'));
}

main().catch((err) => {
    logger.error(err, 'Failed to start server');
    process.exit(1);
});
