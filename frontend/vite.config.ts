import { defineConfig, Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import fs from 'fs';

// Copy pdf.js worker to dist with a stable filename (no content hash)
// so it survives redeploys without stale-cache 404s
function copyPdfWorker(): Plugin {
  return {
    name: 'copy-pdf-worker',
    writeBundle(options) {
      const outDir = options.dir || 'dist';
      const workerSrc = path.resolve(
        __dirname,
        'node_modules/pdfjs-dist/build/pdf.worker.min.mjs',
      );
      const workerDest = path.resolve(outDir, 'pdf.worker.min.mjs');
      if (fs.existsSync(workerSrc)) {
        fs.copyFileSync(workerSrc, workerDest);
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), copyPdfWorker()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  optimizeDeps: {
    include: ['react-pdf'],
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
