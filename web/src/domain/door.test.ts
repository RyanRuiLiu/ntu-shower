import { describe, expect, it } from 'vitest'

import type { DoorState } from './door'
import { DOOR_STATE_LABEL, isAvailable, isOccupied } from './door'

/**
 * 服务端 `DoorState` 的全部取值。
 *
 * 这里**手写列出**而不是从 `DOOR_STATE_LABEL` 推导：如果从它推导，那么漏掉一个状态时
 * 测试会跟着一起漏（两边同时少一个键，断言仍然通过）。手写清单才能在服务端新增
 * 状态而前端忘记登记时失败。
 */
const ALL_STATES: DoorState[] = [
  'free',
  'waiting',
  'bathing',
  'paused',
  'selected',
  'repair',
  'disabled',
]

describe('DOOR_STATE_LABEL', () => {
  it('覆盖服务端的全部状态', () => {
    expect(Object.keys(DOOR_STATE_LABEL).sort()).toEqual([...ALL_STATES].sort())
  })

  it.each(ALL_STATES)('%s 有非空的中文文案', (state) => {
    const label = DOOR_STATE_LABEL[state]
    expect(label).toBeTruthy()
    expect(label).toMatch(/[\u4e00-\u9fa5]/)
  })
})

describe('isAvailable', () => {
  it('只有 free 可用', () => {
    expect(isAvailable('free')).toBe(true)
  })

  // 参数化列出其余六种，避免只测一两个就以为覆盖了
  it.each(ALL_STATES.filter((s) => s !== 'free'))('%s 不可用', (state) => {
    expect(isAvailable(state)).toBe(false)
  })
})

describe('isOccupied', () => {
  it.each(['waiting', 'bathing', 'paused'] as const)('%s 表示有人占用', (state) => {
    expect(isOccupied(state)).toBe(true)
  })

  // 故障与停用**不算**占用：对用户来说"等一会儿"和"别等了"是两种不同的行动建议
  it.each(['free', 'selected', 'repair', 'disabled'] as const)('%s 不算占用', (state) => {
    expect(isOccupied(state)).toBe(false)
  })

  it('可用与占用互斥', () => {
    for (const state of ALL_STATES) {
      expect(isAvailable(state) && isOccupied(state)).toBe(false)
    }
  })
})
