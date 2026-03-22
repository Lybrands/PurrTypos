import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          const n = id.replace(/\\/g, '/')
          if (!n.includes('node_modules')) return
          if (n.includes('@lexical') || n.includes('lexical/')) return 'lexical'
          if (n.includes('@tiptap')) return 'tiptap'
          if (n.includes('simple-mind-map')) return 'mindmap'
          if (n.includes('react-markdown') || n.includes('remark-') || n.includes('rehype-') || n.includes('marked')) return 'markdown'
          if (n.includes('openai')) return 'openai'
          if (n.includes('sql.js')) return 'sql'
          if (n.includes('turndown')) return 'turndown'
          if (n.includes('adm-zip')) return 'misc'
          return 'react-antd'
        },
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
})
