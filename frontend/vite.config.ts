import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Split dev mode (`make frontend` on :5173 + `make api` on :8000): proxy
  // /api to the backend so the tile's same-origin fetch works. In prod the
  // FastAPI app serves the built SPA, so there's no proxy there.
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
