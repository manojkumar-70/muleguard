import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    open: true,
    proxy: {
      '/v1': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/summary': 'http://127.0.0.1:8000',
      '/alerts': 'http://127.0.0.1:8000',
      '/accounts': 'http://127.0.0.1:8000',
    },
  },
});

