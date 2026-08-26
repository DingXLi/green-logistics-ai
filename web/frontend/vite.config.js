import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    // iter #5: code-splitting. 大型组件 (Dashboard 子组件, Map) 改成 dynamic import
    // 减小初始 bundle size, 加快 first paint
    rollupOptions: {
      output: {
        manualChunks: {
          // 第三方 vendor chunks — 单独 cache 提升后续 build 命中率
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
          'vendor-recharts': ['recharts'],
          'vendor-leaflet': ['leaflet', 'react-leaflet'],
        },
        // 大型组件单独 chunk
        chunkFileNames: (chunkInfo) => {
          // 默认 assets/[name]-[hash].js
          if (chunkInfo.name?.includes('Dashboard') || chunkInfo.name?.includes('Map')) {
            return 'assets/components/[name]-[hash].js'
          }
          return 'assets/[name]-[hash].js'
        },
      },
    },
    // 提高 chunk size 警告阈值 (我们刻意分成 vendor chunks, 单个可能接近 500kB)
    chunkSizeWarningLimit: 800,
  },
})
