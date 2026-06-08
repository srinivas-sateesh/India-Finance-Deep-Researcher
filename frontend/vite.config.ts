import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: parseInt(process.env.FRONTEND_PORT || '5173'),
    proxy: {
      '/research': process.env.BACKEND_URL || 'http://localhost:8001',
    },
  },
})
