import { z } from 'zod';
import dotenv from 'dotenv';

// Load .env file
dotenv.config();

const envSchema = z.object({
    PORT: z.coerce.number().default(3000),
    NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
    ALLOW_DEMO_ROUTES: z
        .enum(['true', 'false'])
        .optional()
        .transform((value) => value === 'true'),

    // Comma-separated list of allowed CORS origins for the deployed frontend
    // (e.g. "https://anizai.ai,https://www.anizai.ai"). Required in production —
    // localhost dev origins are only allowed outside production (KG-C-10b).
    CORS_ORIGINS: z
        .string()
        .optional()
        .transform((value) =>
            (value ?? '')
                .split(',')
                .map((origin) => origin.trim())
                .filter(Boolean)
        ),

    // Firebase Project ID (required for ADC initialization)
    FIREBASE_PROJECT_ID: z.string().min(1),
});

const parsed = envSchema.safeParse(process.env);

if (!parsed.success) {
    // eslint-disable-next-line no-console
    console.error('❌ Invalid environment variables:', parsed.error.flatten().fieldErrors);
    process.exit(1);
}

export const env = parsed.data;

export const isDev = env.NODE_ENV === 'development';
export const isProd = env.NODE_ENV === 'production';
export const isTest = env.NODE_ENV === 'test';
