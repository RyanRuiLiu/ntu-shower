import { resolve } from 'node:path'

import { defineConfig } from 'vitest/config'

/**
 * 测试配置。
 *
 * 独立于 Taro 的 `config/index.ts`：那份描述的是**产物如何编译**（多端、小程序
 * 平台差异），测试只关心**纯逻辑能否在 Node 环境里验证**。两者混在一起会让
 * "跑一次单测"依赖完整的 Taro 编译链。
 *
 * 不用 `@vitejs/plugin-react`：它的类型基于 rollup 4，而项目 Vite 4 带 rollup 3，
 * 两套 `Plugin` 类型无法赋值（vitest 自带 Vite 5，与项目 Vite 4 隔离）。
 * JSX 由 vitest 内置的 esbuild 转换，见下方 esbuild 配置——够用且少一层依赖。
 */
export default defineConfig({
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  esbuild: {
    jsx: 'automatic',
  },
  test: {
    environment: 'jsdom',
    // 测试与源码同目录（*.test.ts / *.test.tsx），就近放置
    include: ['src/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        // 页面与入口：由 Taro 运行时驱动，Node 环境测不出真实行为
        'src/pages/**',
        'src/app.ts',
        'src/app.config.ts',
      ],
    },
  },
})
