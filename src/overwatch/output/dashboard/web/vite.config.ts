import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// SPA build for the Overwatch operator console (#124, ADR-0008).
//
// Builds to dist/ (gitignored). CI builds this bundle and ships it to the Jetson,
// which serves it statically via the Python backend — the device never runs Node
// or this build. In dev, the API is proxied to the local Python backend.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8080',
    },
  },
})
