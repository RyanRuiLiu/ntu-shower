import type { UserConfigExport } from '@tarojs/cli'

export default {
  mini: {},
  h5: {
    /**
     * H5 开发代理。
     *
     * `.env.development` 的 API 基址留空 → 客户端发相对路径 → 由这里转发到本地
     * 服务端。这样开发期的同源行为（相对路径解析、无跨域预检）与生产一致，
     * 不必为开发环境在服务端开口 CORS。
     *
     * WebSocket（实时推送）同样要代理，否则升级请求会被浏览器按跨域拦下。
     */
    devServer: {
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8001',
          changeOrigin: true,
        },
      },
    },
  },
} satisfies UserConfigExport<'vite'>
