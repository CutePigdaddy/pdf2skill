import { defineConfig } from 'vite';
import dts from 'vite-plugin-dts';

export default defineConfig({
  plugins: [
    dts({
      entryRoot: 'src',
      outDir: 'dist',
    }),
  ],
  build: {
    lib: {
      entry: 'src/index.ts',
      name: 'Pdf2Skill',
      formats: ['es', 'cjs'],
      fileName: (format) => (format === 'es' ? 'pdf2skill.js' : 'pdf2skill.cjs'),
    },
    rollupOptions: {
      external: ['jszip', 'pdf-lib'],
    },
  },
  test: {
    environment: 'node',
    include: ['test/**/*.test.ts'],
  },
});
