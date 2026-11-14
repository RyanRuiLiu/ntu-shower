/**
 * 服务端 API 客户端——前端唯一的出网口。
 *
 * 视图组件不得直接 fetch：路径、鉴权、错误解包各写一份必漂移。所有请求走这里，
 * 服务端一律 RFC 9457 problem+json，统一解包为带 status 的 ApiError。
 * 数据获取走 TanStack Query（queries.ts），这里只提供传输层。
 */

export class ApiError extends Error {
  status: number;
  problem: { type: string; title: string; detail?: string } | null;

  constructor(status: number, problem: { type: string; title: string; detail?: string } | null) {
    super(problem?.detail || problem?.title || `服务端返回了无法解析的错误（HTTP ${status}）`);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }
}

export const API_V1 = "/api/v1";

export const devicePath = (id: string, suffix = "") =>
  `${API_V1}/devices/${encodeURIComponent(id)}${suffix}`;

/** RFC 9457 problem+json 解包：非 2xx 抛 ApiError，401 由调用方引导重登录。 */
async function send<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const hasBody = options.body !== undefined;
  if (hasBody && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (!res.ok) {
    const problem = await res.json().catch(() => null);
    throw new ApiError(res.status, problem);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const jsonBody = (value: unknown): RequestInit => ({ body: JSON.stringify(value ?? {}) });

export const get = <T>(path: string): Promise<T> => send<T>(path);
export const post = <T>(path: string, value?: unknown, init?: RequestInit): Promise<T> => {
  const { body, headers, ...rest } = jsonBody(value);
  const merged = { ...rest, ...init, body, headers: { ...headers, ...(init?.headers ?? {}) } };
  return send<T>(path, { method: "POST", ...merged });
};
export const postForm = <T>(path: string, form: FormData): Promise<T> =>
  send<T>(path, { method: "POST", body: form });
export const put = <T>(path: string, value: unknown): Promise<T> =>
  send<T>(path, { method: "PUT", ...jsonBody(value) });
export const patch = <T>(path: string, value: unknown): Promise<T> =>
  send<T>(path, { method: "PATCH", ...jsonBody(value) });
export const del = <T>(path: string): Promise<T> => send<T>(path, { method: "DELETE" });
