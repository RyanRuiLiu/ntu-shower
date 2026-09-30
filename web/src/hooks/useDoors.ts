import { useCallback, useEffect, useState } from 'react'

import { getDoors } from '../api/client'
import type { DoorView } from '../domain/door'

export interface DoorsState {
  doors: DoorView[]
  loading: boolean
  /** 失败原因（面向用户的文案）。为 null 表示没有错误。 */
  error: string | null
  reload: () => void
}

/**
 * 拉取喷头现态。
 *
 * 抽成 hook 而非写在页面里，是为了**可测**：页面组件由 Taro 运行时驱动，
 * 在 Node 环境测不出真实行为，而本 hook 是纯 React，可以直接渲染验证。
 *
 * 竞态处理分两层：
 * - `current` 标志在 effect 清理时置假，丢弃已卸载组件或已被新 effect 取代的响应；
 * - `ticket` 只是触发 effect 重跑的计数器（`reload` 用），本身不参与判定。
 */
export function useDoors(identity?: string): DoorsState {
  const [doors, setDoors] = useState<DoorView[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [ticket, setTicket] = useState(0)

  const reload = useCallback(() => setTicket((n) => n + 1), [])

  useEffect(() => {
    let current = true

    setLoading(true)
    setError(null)

    getDoors(identity)
      .then((data) => {
        if (!current) return
        setDoors(data)
      })
      .catch((err: unknown) => {
        if (!current) return
        setError(err instanceof Error ? err.message : '无法获取喷头状态')
      })
      .finally(() => {
        if (!current) return
        setLoading(false)
      })

    return () => {
      current = false
    }
  }, [identity, ticket])

  return { doors, loading, error, reload }
}
