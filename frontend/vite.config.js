import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

// 开发端口 5173（被占用时 Vite 自动 +1），接口代理到 FastAPI。
// 本机 8000 被无关进程长期占用，后端开发默认跑 8010，可用环境变量覆盖：
//   XIAOYI_API_ORIGIN=http://127.0.0.1:8000 npm run dev
const apiOrigin = process.env.XIAOYI_API_ORIGIN || 'http://127.0.0.1:8010'

// dev 与 preview 共用的代理：/api /audio /photos 到 FastAPI，/ws 到 WS
const proxy = {
  '/api': { target: apiOrigin, changeOrigin: true },
  '/audio': { target: apiOrigin, changeOrigin: true },
  '/photos': { target: apiOrigin, changeOrigin: true },
  '/ws': { target: apiOrigin.replace(/^http/, 'ws'), ws: true },
}

export default defineConfig({
  plugins: [
    vue(),
    // PWA：可安装到桌面 + 离线打开外壳（老人一键打开，像本地 App）
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['pwa-192.png', 'pwa-512.png'],
      manifest: {
        name: '小忆 · 暖心陪伴',
        short_name: '小忆',
        description: '暖心陪伴，就在您身边',
        lang: 'zh-CN',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        background_color: '#FEF8ED',
        theme_color: '#C88A52',
        icons: [
          { src: 'pwa-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512.png', sizes: '512x512', type: 'image/png' },
        ],
      },
      workbox: {
        // 网络优先 + 失败回缓存：动态接口请求不能吃缓存数据，
        // 但外壳（JS/CSS/图标）预缓存保证断网也能打开界面
        navigateFallback: '/index.html',
        runtimeCaching: [],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy,
  },
  preview: {
    port: 5180,
    proxy,
  },
})
