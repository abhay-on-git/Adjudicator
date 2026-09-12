import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Forward /api/* to Django in dev so the frontend can use plain
      // relative paths (src/api.ts) that also work unchanged if this is
      // ever served from behind the same origin as Django in production.
      // Avoids needing django-cors-headers for local development.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
