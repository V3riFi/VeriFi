import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// `npm run build:demo` (mode "demo") builds the online demo: the backend is
// simulated in the browser (src/demo/fakeBackend.js) and the files land in
// dist-demo/ with fixed names, ready to publish as a static page.
export default defineConfig(({ mode }) => {
  const demo = mode === 'demo'
  return {
    plugins: [
      react(),
      demo && {
        name: 'demo-title',
        transformIndexHtml: (html) => html.replace('<title>VeriFi</title>', '<title>VeriFi Operator Demo</title>'),
      },
    ],
    base: demo ? './' : '/',
    build: demo
      ? {
          outDir: 'dist-demo',
          rollupOptions: {
            output: { entryFileNames: 'assets/app.js', assetFileNames: 'assets/[name][extname]' },
          },
        }
      : {},
  }
})
