import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// SKYGYM frontend build.
//  - dev:  Vite proxies /api to the FastAPI backend on :8000 (DECISIONS D018)
//  - prod: the build lands in frontend/dist and FastAPI serves it as static files
//
// The simulation runs in the browser: the app boots Pyodide from
// `public/pyodide` and installs `app.core` from `public/pyapp`, both of which
// `npm run sync:core` regenerates. Neither is bundled - the browser loads them
// from its own origin, so the app stays offline-first.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  optimizeDeps: {
    // The browser loads pyodide from `public/pyodide` at runtime; it must not
    // be pre-bundled by the dev server.
    exclude: ['pyodide'],
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1400,
    rollupOptions: {
      output: {
        manualChunks: {
          three: ['three', '@react-three/fiber', '@react-three/drei'],
          charts: ['recharts'],
        },
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    include: ['src/**/*.test.{ts,tsx}'],
  },
})