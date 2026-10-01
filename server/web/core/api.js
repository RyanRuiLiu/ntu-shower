/**
 * 服务端 API 客户端——前端唯一的出网口。
 *
 * 视图模块不得直接 `fetch`：路径、鉴权头、错误解包各写一份，改一处就会漏三处。
 *
 * ## 走哪组接口
 *
 * - `/ops`：本页的全部读写（喷头现态、会话操作、设备、配置、身份、发布、审计），
 *   需 `X-Ops-Token`
 * - `/ota`：版本清单与 APK 下载（不挂在 `/ops` 下）
 *
 * 小程序另走 `/api/v1`（`X-Identity` + 能力位），**那个前端不在本仓库**。服务端的
 * 喷头接口在两个域返回同样的字段，这里选 `/ops` 只是因为写操作也在那儿。
 *
 * ## 错误
 *
 * 服务端一律返回 RFC 9457 问题详情，`title`/`detail` 已是可直接展示的中文。
 * 这里把两者合成一个带 `status` 的 Error，页面只管 catch 后显示 message。
 */
import { syncClock } from './clock.js'

const OPS_API = '/ops'
const OTA_API = '/ota'
/**
 * 访问令牌。这个页面只给开发者一个人用，令牌存 localStorage：
 * 刷新页面不该每次重输。
 */
const TOKEN_KEY = 'ntu-shower.ops-token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(value) {
  if (value) localStorage.setItem(TOKEN_KEY, value)
  else localStorage.removeItem(TOKEN_KEY)
}

export function hasToken() {
  return getToken() !== ''
}

/**
 * 发一次请求。
 *
 * `raw: true` 用于 FormData：那种请求的 Content-Type 必须由浏览器按 boundary 自己设，
 * 预先写死 `application/json` 会让服务端解析不出任何字段。
 */
async function send(base, path, options = {}) {
  const { raw = false, ...rest } = options
  const headers = { ...(rest.headers || {}) }
  if (!raw) headers['Content-Type'] = 'application/json'
  if (base === OPS_API && hasToken()) headers['X-Ops-Token'] = getToken()

  const res = await fetch(base + path, { ...rest, headers })
  syncClock(res)
  if (!res.ok) {
    const problem = await res.json().catch(() => null)
    const error = new Error(problem?.detail || problem?.title || `HTTP ${res.status}`)
    error.status = res.status
    error.problem = problem
    throw error
  }
  return res.status === 204 ? null : res.json()
}

function query(params) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

const body = (value) => ({ body: JSON.stringify(value ?? {}) })
const post = (value) => ({ method: 'POST', ...body(value) })
const put = (value) => ({ method: 'PUT', ...body(value) })
const patch = (value) => ({ method: 'PATCH', ...body(value) })
const del = () => ({ method: 'DELETE' })

export const api = {
  // ── 喷头现态 ──
  //
  // 服务端有两个端点返回**同样的字段**（`/api/v1/doors` 与 `/ops/doors`，后者
  // 只是多一道令牌校验），网页端走管理面那条：它与写操作同域，且带 `device_id` 过滤。
  doors: (deviceId) => send(OPS_API, `/doors${query({ device_id: deviceId })}`),
  // ── 会话操作 ──
  //
  // 走 `/ops`：这个页面是开发者在代操作，凭 `X-Ops-Token` 授权。小程序那侧走
  // `/api/v1/sessions/*`，需要 `X-Identity` 与能力位——两边共用同一份领域逻辑，
  // 区别只在怎么授权。
  assign: (value) => send(OPS_API, '/sessions/assign', post(value)),
  settle: (value) => send(OPS_API, '/sessions/settle', post(value)),
  photo: (value) => send(OPS_API, '/sessions/photo', post(value)),

  // ── 设备档案 ──
  devices: () => send(OPS_API, '/devices'),
  patchDevice: (id, value) => send(OPS_API, `/devices/${encodeURIComponent(id)}`, patch(value)),
  shadow: (id) => send(OPS_API, `/devices/${encodeURIComponent(id)}/shadow`),

  // ── 设备动作 ──
  exec: (id, command) => send(OPS_API, `/devices/${encodeURIComponent(id)}/exec`, post({ command })),
  capture: (id) => send(OPS_API, `/devices/${encodeURIComponent(id)}/capture`, post()),
  restart: (id) => send(OPS_API, `/devices/${encodeURIComponent(id)}/restart`, post()),
  uninstall: (id) => send(OPS_API, `/devices/${encodeURIComponent(id)}/uninstall`, post()),
  dumpTree: (id) => send(OPS_API, `/devices/${encodeURIComponent(id)}/dump`, post()),
  touch: (id, value) => send(OPS_API, `/devices/${encodeURIComponent(id)}/touch`, post(value)),
  scroll: (id, direction) => send(OPS_API, `/devices/${encodeURIComponent(id)}/scroll`, post({ direction })),
  key: (id, key) => send(OPS_API, `/devices/${encodeURIComponent(id)}/key`, post({ key })),
  // channel 必填："clipboard"（粘贴，支持中文）或 "keys"（按键注入，仅 ASCII）
  input: (id, text, channel) =>
    send(OPS_API, `/devices/${encodeURIComponent(id)}/input`, post({ text, channel })),

  // ── 调试通道 ──
  bridgeStatus: (id) => send(OPS_API, `/devices/${encodeURIComponent(id)}/bridge/status`),
  bridgeOpen: (id) => send(OPS_API, `/devices/${encodeURIComponent(id)}/bridge/open`, post()),
  bridgeClose: (id) => send(OPS_API, `/devices/${encodeURIComponent(id)}/bridge/close`, post()),

  // ── 配置 ──
  configSpec: () => send(OPS_API, '/config/spec'),
  globalConfig: () => send(OPS_API, '/config/global'),
  saveGlobalConfig: (config) => send(OPS_API, '/config/global', put({ config })),
  deviceConfig: (id) => send(OPS_API, `/devices/${encodeURIComponent(id)}/config`),
  saveDeviceConfig: (id, config) => send(OPS_API, `/devices/${encodeURIComponent(id)}/config`, put({ config })),
  clearDeviceConfig: (id) => send(OPS_API, `/devices/${encodeURIComponent(id)}/config`, del()),

  // ── 身份 ──
  identities: () => send(OPS_API, '/identities'),
  createIdentity: (value) => send(OPS_API, '/identities', post(value)),
  deleteIdentity: (id) => send(OPS_API, `/identities/${encodeURIComponent(id)}`, del()),

  // ── 发布 ──
  releases: () => send(OTA_API, '/releases'),
  uploadRelease: (form) => send(OPS_API, '/releases', { method: 'POST', body: form, raw: true }),
  deleteRelease: (version) => send(OPS_API, `/releases/${encodeURIComponent(version)}`, del()),
  rearmRelease: (version, deviceId) =>
    send(OPS_API, `/releases/${encodeURIComponent(version)}/rearm/${encodeURIComponent(deviceId)}`, post()),

  // ── 审计 ──
  commands: (params) => send(OPS_API, `/commands${query(params)}`),
}
