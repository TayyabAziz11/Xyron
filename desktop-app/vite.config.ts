import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

const host = process.env.TAURI_DEV_HOST

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? { protocol: 'ws', host, port: 1421 }
      : undefined,
    watch: { ignored: ['**/src-tauri/**'] },
    // Allow Vite to serve files from the shared/ directory (../shared relative to desktop-app/).
    // Without this, every shared component/hook returns a 403.
    fs: {
      allow: ['..'],
    },
    // Forward /api/* and WebSocket upgrades to FastAPI backend.
    // Shared hooks use relative URLs (Next.js SSR pattern) — proxy fixes them in Tauri.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
    // COOP + COEP required for SharedArrayBuffer (used by ONNX Runtime / VAD model).
    // 'credentialless' COEP avoids breaking cross-origin fetches without CORP headers.
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'credentialless',
    },
  },
  // Shared code uses process.env.NEXT_PUBLIC_API_URL (Next.js convention).
  // Vite doesn't define process — shim it so the localhost:8000 fallback kicks in.
  define: {
    'process.env': {},
  },
  envPrefix: ['VITE_', 'TAURI_ENV_*'],
  build: {
    target: ['chrome105', 'edge105'],
    minify: !process.env.TAURI_ENV_DEBUG ? ('esbuild' as const) : false,
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          router: ['react-router-dom'],
          motion: ['framer-motion'],
          icons: ['lucide-react'],
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '../shared'),
      '@desktop': path.resolve(__dirname, 'src'),
    },
  },
})
