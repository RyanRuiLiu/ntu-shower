import Taro from '@tarojs/taro'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, getDoors, request } from './client'

vi.mock('@tarojs/taro', () => ({
  default: { request: vi.fn() },
}))

const mockedRequest = vi.mocked(Taro.request)

describe('request', () => {
  beforeEach(() => {
    mockedRequest.mockReset()
  })

  it('成功时返回响应体', async () => {
    mockedRequest.mockResolvedValue({ statusCode: 200, data: { ok: true } } as never)

    await expect(request<{ ok: boolean }>('/api/v1/doors')).resolves.toEqual({ ok: true })
  })

  it('拼上 API 基址与 Content-Type', async () => {
    mockedRequest.mockResolvedValue({ statusCode: 200, data: [] } as never)

    await request('/api/v1/doors')

    expect(mockedRequest).toHaveBeenCalledWith(
      expect.objectContaining({
        url: expect.stringContaining('/api/v1/doors'),
        method: 'GET',
        header: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    )
  })

  it('带 identity 时附加 X-Identity 头', async () => {
    mockedRequest.mockResolvedValue({ statusCode: 200, data: [] } as never)

    await request('/api/v1/doors', { identity: 'openid-abc' })

    expect(mockedRequest).toHaveBeenCalledWith(
      expect.objectContaining({
        header: expect.objectContaining({ 'X-Identity': 'openid-abc' }),
      }),
    )
  })

  it('不带 identity 时不发该头', async () => {
    mockedRequest.mockResolvedValue({ statusCode: 200, data: [] } as never)

    await request('/api/v1/doors')

    const arg = mockedRequest.mock.calls[0]?.[0] as { header: Record<string, string> }
    expect(arg.header['X-Identity']).toBeUndefined()
  })

  it('4xx 时抛 ApiError 并解析 RFC 9457 详情', async () => {
    mockedRequest.mockResolvedValue({
      statusCode: 409,
      data: {
        type: 'https://ntu-shower.iseekdream.com/problems/device-busy',
        title: 'Device busy',
        status: 409,
        detail: '设备已有在途命令',
      },
    } as never)

    await expect(request('/api/v1/sessions/assign')).rejects.toThrow(ApiError)
    await expect(request('/api/v1/sessions/assign')).rejects.toThrow('设备已有在途命令')
  })

  it('错误体不是 problem+json 时退回状态码，不吞掉失败', async () => {
    mockedRequest.mockResolvedValue({ statusCode: 502, data: '<html>Bad Gateway</html>' } as never)

    const error = await request('/api/v1/doors').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(502)
    expect((error as ApiError).problem).toBeUndefined()
  })

  it('问题体没有 detail 时用 title 作为消息', async () => {
    mockedRequest.mockResolvedValue({
      statusCode: 404,
      data: { type: 'https://x/problems/not-found', title: 'Not Found', status: 404 },
    } as never)

    await expect(request('/api/v1/doors')).rejects.toThrow('Not Found')
  })

  it('问题体既无 detail 也无 title 时用兜底文案', async () => {
    mockedRequest.mockResolvedValue({
      statusCode: 500,
      // type 与 status 齐备（能通过 isProblem），但两个可读字段都缺
      data: { type: 'https://x/problems/internal', status: 500 },
    } as never)

    await expect(request('/api/v1/doors')).rejects.toThrow('请求失败')
  })
})

describe('getDoors', () => {
  beforeEach(() => {
    mockedRequest.mockReset()
    mockedRequest.mockResolvedValue({ statusCode: 200, data: [] } as never)
  })

  it('请求喷头现态端点', async () => {
    await getDoors()

    expect(mockedRequest).toHaveBeenCalledWith(
      expect.objectContaining({ url: expect.stringContaining('/api/v1/doors') }),
    )
  })
})
