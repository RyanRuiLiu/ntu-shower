import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { DoorView } from '../domain/door'
import { DoorWall } from './DoorWall'

function makeDoor(doorNo: number, state: DoorView['state'] = 'free'): DoorView {
  return {
    device_id: 'dev-1',
    door_no: doorNo,
    state,
    card_no: 0,
    cert_no: '',
    bath_time_s: 0,
    reported_at: '2026-09-30T12:00:00.000Z',
    device_online: true,
    device_name: '浴室终端',
  }
}

describe('DoorWall', () => {
  it('每扇门渲染一块门牌', () => {
    const { container } = render(
      <DoorWall doors={[makeDoor(1), makeDoor(2), makeDoor(3)]} />,
    )

    expect(container.querySelectorAll('.door')).toHaveLength(3)
  })

  it('保留服务端给出的门号', () => {
    render(<DoorWall doors={[makeDoor(1), makeDoor(7)]} />)

    expect(screen.getByText('1')).toBeTruthy()
    expect(screen.getByText('7')).toBeTruthy()
  })

  it('空列表给出说明而非空白', () => {
    const { container } = render(<DoorWall doors={[]} />)

    expect(container.querySelector('.wall--empty')).toBeTruthy()
    expect(screen.getByText('设备尚未上报喷头状态')).toBeTruthy()
    // 空态不该渲染门牌
    expect(container.querySelectorAll('.door')).toHaveLength(0)
  })

  it('把 onSelect 透传给每块门牌', () => {
    const { container } = render(<DoorWall doors={[makeDoor(1)]} onSelect={() => {}} />)

    expect(container.querySelector('.door')?.getAttribute('role')).toBe('button')
  })
})
