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
      // Taro 组件需要真实运行时；测试期映射到语义相近的 DOM 标签
      '@tarojs/components': resolve(__dirname, 'src/test/taro-components.tsx'),
    },
  },
  esbuild: {
    jsx: 'automatic',
  },
  test: {
    environment: 'jsdom',
    // 开启后 testing-library 会自动注册 afterEach(cleanup)。
    // 不开的话，同一个文件里多次 render 的 DOM 会累积，`screen.getByText`
    // 会因为匹配到上一轮的残留元素而报 getMultipleElementsFoundError。
    // 注意这不等于要在测试里用全局函数——显式 import { it, expect } 仍然更好。
    globals: true,
    // 测试与源码同目录（*.test.ts / *.test.tsx），就近放置
    include: ['src/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        // 生成物：由 openapi-typescript 从 openapi.json 产出，没有运行时代码
        'src/api/schema.d.ts',
        // 页面与入口：由 Taro 运行时驱动，Node 环境测不出真实行为
        'src/pages/**',
        'src/app.ts',
        'src/app.config.ts',
      ],
      // 门槛只升不降（与服务端 96、设备端 98 同一原则）。
      // 起步 95 是给尚未编写的页面组件留余量；纯逻辑（src/domain）当前已是 100。
      thresholds: {
        statements: 95,
        branches: 95,
        functions: 95,
        lines: 95,
      },
    },
  },
})
