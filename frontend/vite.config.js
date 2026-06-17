import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Forward VITE_BACKEND_URL from the user's .env so `api.js` can read it
// via `import.meta.env.VITE_BACKEND_URL`.
export default defineConfig(({ mode }) => {
  loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    server: {
      port: 5173,
      host: '127.0.0.1',
    },
  }
})
