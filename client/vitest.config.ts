import { defineConfig } from 'vitest/config'

// Standalone test config. Kept separate from vite.config.ts because the pure
// helper tests need no JSX/react transform, and merging would couple the test
// runner's bundled Vite types to the app's Vite types.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
