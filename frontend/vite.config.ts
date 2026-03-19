import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// Bundle size visualiser — only active when ANALYZE=true is set in the environment.
// Run with: ANALYZE=true npm run build
// Output: dist/stats.html
function getPlugins() {
  const plugins: any[] = [react()];
  if (process.env.ANALYZE === 'true') {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { visualizer } = require('rollup-plugin-visualizer');
    plugins.push(visualizer({ open: true, filename: 'dist/stats.html', gzipSize: true }));
  }
  return plugins;
}

export default defineConfig({
  plugins: getPlugins(),
  base: './', // Required for Electron file:// loading
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // recharts bundles d3 + victory-vendor internally (~528 KB minified).
    // That is its irreducible size. Raise the limit to just above it so the
    // build only warns if the chart chunk unexpectedly balloons.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          // Isolating recharts prevents chart updates from busting the app chunk hash
          'vendor-charts': ['recharts'],
        },
      },
    },
  },
});
