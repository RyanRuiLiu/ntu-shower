import Taro from '@tarojs/taro'

import type { DoorView } from '../domain/door'

/** 服务端基址。留空时走相对路径（H5 开发由 devServer 代理）。 */
const API_BASE = process.env.TARO_APP_API_BASE ?? ''

/**
 * 服务端返回的 RFC 9457 问题详情。
 *
 * 服务端所有错误都用这个结构（`application/problem+json`），`type` 是稳定的
 * 问题标识，`detail` 是给人看的说明——判定分支应看 `type`，不要匹配文案。
 */
export interface ApiProblem {
  type: string
  title: string
  status: number
  detail?: string
}

/** 请求失败时抛出，携带服务端的问题详情（网络层失败时为 undefined）。 */
export class ApiError extends Error {
  readonly status: number
  readonly problem?: ApiProblem

  constructor(status: number, problem?: ApiProblem, fallback = '请求失败') {
    super(problem?.detail || problem?.title || fallback)
    this.name = 'ApiError'
    this.status = status
    this.problem = problem
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  data?: unknown
  /** 身份标识，服务端据此判定能力位。查看类接口不需要。 */
  identity?: string
}

/**
 * 发一次请求并解析响应。
 *
 * 用 `Taro.request` 而非 `fetch`：小程序没有 fetch，而 Taro 的封装在 H5 端
 * 会用 XHR，两端行为一致。
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', data, identity } = options

  const res = await Taro.request({
    url: `${API_BASE}${path}`,
    method,
    data,
    header: {
      'Content-Type': 'application/json',
      ...(identity ? { 'X-Identity': identity } : {}),
    },
  })

  if (res.statusCode >= 400) {
    // 服务端保证错误体是 problem+json；解析失败时退回状态码，不吞掉失败
    const problem = isProblem(res.data) ? res.data : undefined
    throw new ApiError(res.statusCode, problem)
  }

  return res.data as T
}

function isProblem(value: unknown): value is ApiProblem {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as ApiProblem).type === 'string' &&
    typeof (value as ApiProblem).status === 'number'
  )
}

/** 查询全部喷头现态（查看类接口，无需身份）。 */
export function getDoors(identity?: string): Promise<DoorView[]> {
  return request<DoorView[]>('/api/v1/doors', { identity })
}
