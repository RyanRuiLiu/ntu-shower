import type { ReactNode } from 'react'

/**
 * `@tarojs/components` 在测试环境的最小替身。
 *
 * Taro 的组件需要真实运行时（小程序渲染层 / H5 的 runtime 适配）才能工作，
 * Node 环境里既不是 DOM 元素也没有对应实现。测试只关心**渲染出的结构能否被
 * 断言**，因此把用到的几个组件映射到语义相近的 DOM 标签。
 *
 * 由 `vitest.config.ts` 的 alias 接入，仅测试期生效，不进产物。
 */

interface ViewProps {
  children?: ReactNode
  className?: string
  onClick?: () => void
  role?: string
  'aria-label'?: string
}

export function View({ children, ...rest }: ViewProps) {
  return <div {...rest}>{children}</div>
}

export function Text({ children, ...rest }: ViewProps) {
  return <span {...rest}>{children}</span>
}
