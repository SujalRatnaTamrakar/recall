import { defineConfig } from 'vite';

const target = 'http://127.0.0.1:8765';
const localProxy = () => ({
  target,
  changeOrigin: true,
  headers: { Origin: target },
});

export default defineConfig({
  server: {
    proxy: {
      '/api': localProxy(),
      '/video': localProxy(),
      '/frames': localProxy(),
      '/media': localProxy(),
    },
  },
  build: { outDir: 'dist', sourcemap: false },
});
