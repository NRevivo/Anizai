import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Separate app from `client/` in every respect: its own build, its own dev
// server port, its own dependency tree. Nothing here is shared, which is the
// point — a shared build is a shared blast radius (plan §2.5 N3).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 127.0.0.1, not `localhost`. On Windows, Node resolves `localhost` to
      // the IPv6 loopback `::1` first, while uvicorn binds IPv4 only by
      // default — so every proxied call fails with `ECONNREFUSED ::1:8000`
      // and the dashboard shows "Internal Server Error" while a perfectly
      // healthy API sits on the next port. Naming the address removes the
      // ambiguity. (Found 2026-07-29.)
      //
      // In production the dashboard is a Hosting site rewriting /api to the
      // Cloud Run service, so none of this applies there.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    css: false,
  },
})
