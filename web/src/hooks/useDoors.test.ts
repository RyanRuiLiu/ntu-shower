import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getDoors } from '../api/client'
import type { DoorView } from '../domain/door'
import { useDoors } from './useDoors'

vi.mock('../api/client', () => ({
  getDoors: vi.fn(),
}))

const mockedGetDoors = vi.mocked(getDoors)

function makeDoor(doorNo: number): DoorView {
  return {
    device_id: 'dev-1',
    door_no: doorNo,
    state: 'free',
    card_no: 0,
    cert_no: '',
    bath_time_s: 0,
    reported_at: '2026-09-30T12:00:00.000Z',
    device_online: true,
    device_name: '浴室终端',
  }
}

describe('useDoors', () => {
  // 必须逐个用例重置：调用次数是本文件多条断言的对象，不重置会让前面用例的
  // 调用累积过来（reload 用例因此看到 4 次而非 1 次）
  beforeEach(() => {
    mockedGetDoors.mockReset()
  })

  it('成功后给出数据并结束加载', async () => {
    mockedGetDoors.mockResolvedValue([makeDoor(1), makeDoor(2)])

    const { result } = renderHook(() => useDoors())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.doors).toHaveLength(2)
    expect(result.current.error).toBeNull()
  })

  it('请求中处于加载态', () => {
    mockedGetDoors.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useDoors())

    expect(result.current.loading).toBe(true)
    expect(result.current.doors).toEqual([])
  })

  it('失败时给出错误文案且不残留旧数据', async () => {
    mockedGetDoors.mockRejectedValue(new Error('无法获取喷头状态'))

    const { result } = renderHook(() => useDoors())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('无法获取喷头状态')
  })

  it('reload 重新发起请求', async () => {
    mockedGetDoors.mockResolvedValue([])

    const { result } = renderHook(() => useDoors())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(mockedGetDoors).toHaveBeenCalledTimes(1)

    // 包在 act 里：reload 会触发状态更新，直接调用会让 React 报
    // "An update to TestComponent inside a test was not wrapped in act(...)"
    await act(async () => {
      result.current.reload()
    })

    await waitFor(() => expect(mockedGetDoors).toHaveBeenCalledTimes(2))
  })

  it('卸载后不再写入状态（避免 React 警告与竞态覆盖）', async () => {
    let resolveLater: (value: DoorView[]) => void = () => {}
    mockedGetDoors.mockReturnValue(
      new Promise<DoorView[]>((resolve) => {
        resolveLater = resolve
      }),
    )

    const { unmount } = renderHook(() => useDoors())
    unmount()
    resolveLater([makeDoor(1)])

    // 若未做卸载保护，这里会触发 "state update on unmounted component" 警告；
    // waitFor 给 promise 链一个完成的时机，断言只是确保没有抛出
    await waitFor(() => expect(mockedGetDoors).toHaveBeenCalled())
  })

  it('请求失败且已卸载时不写入错误（失败路径同样要防竞态）', async () => {
    let rejectLater: (reason: Error) => void = () => {}
    mockedGetDoors.mockReturnValue(
      new Promise<DoorView[]>((_resolve, reject) => {
        rejectLater = reject
      }),
    )

    const { unmount } = renderHook(() => useDoors())
    unmount()
    rejectLater(new Error('网络中断'))

    await waitFor(() => expect(mockedGetDoors).toHaveBeenCalled())
  })

  it('拒绝值不是 Error 时用兜底文案', async () => {
    mockedGetDoors.mockRejectedValue('字符串拒绝')

    const { result } = renderHook(() => useDoors())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('无法获取喷头状态')
  })
})
