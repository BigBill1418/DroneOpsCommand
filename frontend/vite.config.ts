/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import fs from 'fs';

const pkg = JSON.parse(fs.readFileSync(path.resolve(__dirname, 'package.json'), 'utf-8'));

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  optimizeDeps: {
    include: ['react-pdf'],
  },
  // v2.67.0 — Vitest config for Mission Hub redesign contract tests
  // (per ADR-0013: real route coverage, msw-mocked API).
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // v2.67.0 Agent D — disable file-level parallelism. Several facet
    // tests use `vi.mock('react-router-dom', ...)` to stub useParams /
    // useNavigate; Vitest's worker-pool runs files concurrently and
    // these module-scope mocks bleed across files, causing intermittent
    // 5s timeouts on Save tests (most reliably reproduced on
    // MissionDetailsEdit.test.tsx). Sequential execution is ~2x slower
    // (~70s vs ~35s on the worktree machine) but eliminates the flake.
    // If/when the facet tests are refactored to colocate their mocks
    // in beforeEach blocks, this can be removed.
    fileParallelism: false,
    server: {
      deps: {
        // Mantine ships ESM-only; Vitest needs to inline it for jsdom.
        inline: [/@mantine\//, /@tabler\//],
      },
    },
  },
  // FIX-3 (v2.63.9): split the 1.9 MB single index-*.js chunk into a small
  // router shell plus vendor chunks that can be cached across deploys
  // (mantine-core, mantine-rich, leaflet, tiptap, pdf, icons, sentry).
  // App pages are code-split via App.tsx lazy() — their chunks are
  // emitted automatically by Vite.
  build: {
    target: 'es2022',
    rollupOptions: {
      output: {
        manualChunks: {
          'mantine-core': [
            '@mantine/core',
            '@mantine/hooks',
            '@mantine/notifications',
          ],
          'mantine-rich': [
            '@mantine/dates',
            '@mantine/dropzone',
            '@mantine/form',
            '@mantine/tiptap',
          ],
          'leaflet': ['leaflet', 'react-leaflet'],
          'tiptap': [
            '@tiptap/react',
            '@tiptap/starter-kit',
            '@tiptap/extension-highlight',
            '@tiptap/extension-link',
            '@tiptap/extension-text-align',
            '@tiptap/extension-underline',
          ],
          'pdf': ['react-pdf'],
          'icons': ['@tabler/icons-react'],
          'sentry': ['@sentry/react'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/uploads': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/static': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
