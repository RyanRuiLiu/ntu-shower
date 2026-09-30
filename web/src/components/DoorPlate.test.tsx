import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { DoorView } from '../domain/door'
import { DoorPlate } from './DoorPlate'

/** 构造一扇门；只覆盖断言用到的字段，其余给合理默认值。 */
function makeDoor(overrides: Partial<DoorView> = {}): DoorView {
  return {
    device_id: 'dev-1',
    door_no: 3,
    state: 'free',
    card_no: 0,
    cert_no: '',
    bath_time_s: 0,
    reported_at: '2026-09-30T12:00:00.000Z',
    device_online: true,
    device_name: '浴室终端',
    ...overrides,
  }
}

describe('DoorPlate', () => {
  it('显示门号与状态文案', () => {
    render(<DoorPlate door={makeDoor({ door_no: 5, state: 'bathing' })} />)

    expect(screen.getByText('5')).toBeTruthy()
    expect(screen.getByText('使用中')).toBeTruthy()
  })

  // 每一个状态都要有对应的类名——颜色由 `.door--<state>` 在 SCSS 里决定，
  // 类名缺失会让该状态渲染成无色，而这是静默的（不会报错）。
  it.each([
    ['free', '空闲'],
    ['waiting', '已入门'],
    ['bathing', '使用中'],
    ['paused', '已暂停'],
    ['selected', '选中'],
    ['repair', '报修'],
    ['disabled', '禁用'],
  ] as const)('%s 状态带出门牌类名与文案', (state, label) => {
    const { container } = render(<DoorPlate door={makeDoor({ state })} />)

    expect(container.querySelector(`.door--${state}`)).toBeTruthy()
    expect(screen.getByText(label)).toBeTruthy()
  })

  it('无 onSelect 时不可点击', () => {
    const { container } = render(<DoorPlate door={makeDoor()} />)
    const plate = container.querySelector('.door')

    expect(plate?.getAttribute('role')).toBeNull()
  })

  it('有 onSelect 时点击回调携带该门', () => {
    const onSelect = vi.fn()
    const door = makeDoor({ door_no: 7 })
    const { container } = render(<DoorPlate door={door} onSelect={onSelect} />)

    const plate = container.querySelector('.door')
    expect(plate).toBeTruthy()
    fireEvent.click(plate as Element)

    expect(onSelect).toHaveBeenCalledWith(door)
  })
})
