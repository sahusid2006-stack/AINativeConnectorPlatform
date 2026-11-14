import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    proxy: { '/api': { target: 'http://api:8000', rewrite: (path) => path.replace(/^\/api/, '') } },
  },
});
