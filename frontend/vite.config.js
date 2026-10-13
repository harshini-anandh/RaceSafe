import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    // Recharts makes the single bundle ~600 kB; fine for a local demo.
    chunkSizeWarningLimit: 1000,
  },
});
