import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Deliberately no /api worker proxy here. Vite cannot safely load the
      // Docker secret used by the authenticated production nginx proxy, so a
      // development proxy would be a bare executable caller. Use the running
      // kai-web endpoint on :3001 for worker-backed UI integration tests.
      '/council': {
        target: 'http://kai-council-api:8002',
        changeOrigin: true
      }
    }
  }
})
