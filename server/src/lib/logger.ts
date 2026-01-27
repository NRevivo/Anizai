import pino from 'pino';
import { env, isProd } from '../config/env.js';

const loggerOptions: pino.LoggerOptions = {
    level: isProd ? 'info' : 'debug',
    base: {
        env: env.NODE_ENV,
    },
};

// In development, use pino-pretty for readable logs
// In production, use standard JSON output
export const logger = isProd
    ? pino(loggerOptions)
    : pino({
        ...loggerOptions,
        transport: {
            target: 'pino-pretty',
            options: {
                colorize: true,
                translateTime: 'SYS:standard',
                ignore: 'pid,hostname',
            },
        },
    });

export type Logger = pino.Logger;
