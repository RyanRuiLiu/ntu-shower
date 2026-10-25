/**
 * 服务端 API 客户端——前端唯一的出网口。
 *
 * 视图模块不得直接 `fetch`：路径、鉴权头、错误解包各写一份，改一处就会漏三处。
 *
 * 服务端是单一资源树（路径即资源，权限由能力位守卫决定，见 docs/ARCHITECTURE.md），
 * 这里按域分组封装；管理台身份需持有 admin 与它代操作所需的 session/photo 能力位。
 *
 * ## 错误
 *
 * 服务端一律返回 RFC 9457 问题详情，`title`/`detail` 已是可直接展示的中文。
 * 这里把两者合成一个带 `status` 的 Error，页面只管 catch 后显示 message。
 */
import { syncClock } from './clock.js'

/**
 * 管理台身份标识。这个页面只给运维者用，标识存 localStorage：
 * 刷新页面不该每次重输。
 */
const IDENTITY_KEY = 'ntu-shower.identity'

export function getIdentity() {
  return localStorage.getItem(IDENTITY_KEY) || ''
}

export function setIdentity(value) {
  if (value) localStorage.setItem(IDENTITY_KEY, value)
  else localStorage.removeItem(IDENTITY_KEY)
}

export function hasIdentity() {
  return getIdentity() !== ''
}

/**
 * 发一次请求。
 *
 * `raw: true` 用于 FormData：那种请求的 Content-Type 必须由浏览器按 boundary 自己设，
 * 预先写死 `application/json` 会让服务端解析不出任何字段。
 */
async function send(path, options = {}) {
  const { raw = false, ...rest } = options
  const headers = { ...(rest.headers || {}) }
  if (!raw) headers['Content-Type'] = 'application/json'
  if (hasIdentity()) headers['X-Identity'] = getIdentity()

  const res = await fetch(path, { ...rest, headers })
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
const API_V1 = '/api/v1'
const devicePath = (id, suffix = '') => `${API_V1}/devices/${encodeURIComponent(id)}${suffix}`

export const api = {
  // ── 喷头 ──（公开；带 device_id 即单设备视图）
  doors: (deviceId) => send(`${API_V1}/doors${query({ device_id: deviceId })}`),

  // ── 会话操作 ──（session/photo 能力位；管理台身份持有它们代操作）
  assign: (deviceId, value) => send(devicePath(deviceId, '/assign'), post(value)),
  settle: (deviceId, value) => send(devicePath(deviceId, '/settle'), post(value)),
  photo: (deviceId) => send(devicePath(deviceId, '/photo'), post()),

  // ── 设备 ──
  // devices: 精简清单（含本身份的 actions）；runtime: 运维档案（admin）
  devices: () => send(`${API_V1}/devices`),
  deviceRuntime: () => send(`${API_V1}/devices/runtime`),
  /** 设备最近一次上送的日志：kind 取 device / crash / ota。 */
  deviceLogs: (id, kind) => send(devicePath(id, `/logs?kind=${encodeURIComponent(kind)}`)),
  /** 从设备现读日志尾部：行数上限由服务端默认值决定，设备离线时抛错。 */
  deviceLogLive: (id, kind) => send(devicePath(id, `/logs/live${query({ kind })}`)),
  /** 设备展示顺序：全部设备清单（下拉、设备表、统计）照此排列。 */
  saveDeviceOrder: (order) => send(`${API_V1}/devices/order`, put({ order })),
  patchDevice: (id, value) => send(devicePath(id), patch(value)),

  // ── 设备动作 ──（admin）
  exec: (id, command) => send(devicePath(id, '/exec'), post({ command })),
  capture: (id) => send(devicePath(id, '/capture'), post()),
  appRestart: (id) => send(devicePath(id, '/app/restart'), post()),
  reboot: (id) => send(devicePath(id, '/reboot'), post()),
  bathRestart: (id) => send(devicePath(id, '/bath/restart'), post()),
  clockSet: (id, datetime) => send(devicePath(id, '/clock/set'), post({ datetime })),
  dumpTree: (id) => send(devicePath(id, '/dump'), post()),
  touch: (id, value) => send(devicePath(id, '/touch'), post(value)),
  scroll: (id, direction) => send(devicePath(id, '/scroll'), post({ direction })),
  key: (id, key) => send(devicePath(id, '/key'), post({ key })),
  // channel 必填："clipboard"（粘贴，支持中文）或 "keys"（按键注入，仅 ASCII）
  input: (id, text, channel) => send(devicePath(id, '/input'), post({ text, channel })),

  // ── 免费名单 ──（admin）
  // 读回的是行、交出去的也是行：文件的行尾形状由服务端负责，页面不碰它。
  freelist: (id) => send(devicePath(id, '/freelist')),
  saveFreelist: (id, students) => send(devicePath(id, '/freelist'), put({ students })),

  // ── 调试通道 ──（admin）
  bridgeStatus: (id) => send(devicePath(id, '/bridge')),
  bridgeOpen: (id) => send(devicePath(id, '/bridge/open'), post()),
  bridgeClose: (id) => send(devicePath(id, '/bridge/close'), post()),

  // ── 配置 ──（admin）
  configSpec: () => send(`${API_V1}/config/spec`),
  globalConfig: () => send(`${API_V1}/config`),
  saveGlobalConfig: (config) => send(`${API_V1}/config`, put({ config })),
  deviceConfig: (id) => send(devicePath(id, '/config')),
  saveDeviceConfig: (id, config) => send(devicePath(id, '/config'), put({ config })),
  clearDeviceConfig: (id) => send(devicePath(id, '/config'), del()),

  // ── 语音播报 ──（admin；规则目标 = 设备 × 学号，各自可具体或「全部」）
  voiceSpec: () => send(`${API_V1}/voice/spec`),
  /** device_id 省略列全部；传 '*' 只列「全部设备」的通配规则。 */
  voiceRules: (deviceId) => send(`${API_V1}/voice/rules${query({ device_id: deviceId })}`),
  /** 一次可展开多设备 × 多学号；已存在的组合由服务端跳过并在响应里点名。 */
  createVoiceRule: (value) => send(`${API_V1}/voice/rules`, post(value)),
  updateVoiceRule: (id, value) => send(`${API_V1}/voice/rules/${id}`, put(value)),
  patchVoiceRule: (id, value) => send(`${API_V1}/voice/rules/${id}`, patch(value)),
  deleteVoiceRule: (id) => send(`${API_V1}/voice/rules/${id}`, del()),
  voiceStatus: () => send(`${API_V1}/voice/status`),
  voiceDeliveries: () => send(`${API_V1}/voice/deliveries`),
  previewVoiceRule: (value) => send(`${API_V1}/voice/preview`, post(value)),
  pushVoiceRule: (id, deviceId) => send(`${API_V1}/voice/rules/${id}/push`, post({ device_id: deviceId })),
  // ── 音效库 ──（admin 管理；试听文件按内容寻址公开，同 /voice/clips）
  voiceEffects: () => send(`${API_V1}/voice/effects`),
  uploadVoiceEffect: (form) => send(`${API_V1}/voice/effects`, { method: 'POST', body: form, raw: true }),
  renameVoiceEffect: (id, name) => send(`${API_V1}/voice/effects/${id}`, put({ name })),
  deleteVoiceEffect: (id) => send(`${API_V1}/voice/effects/${id}`, del()),

  // ── 身份 ──（admin；名单响应含能力位枚举，身份页一次装载）
  identities: () => send(`${API_V1}/identities`),
  createIdentity: (value) => send(`${API_V1}/identities`, post(value)),
  updateIdentity: (identity, value) => send(`${API_V1}/identities/${encodeURIComponent(identity)}`, put(value)),
  deleteIdentity: (id) => send(`${API_V1}/identities/${encodeURIComponent(id)}`, del()),

  // ── 发布 ──（读公开；写 admin）
  releases: () => send(`${API_V1}/ota/releases`),
  uploadRelease: (form) => send(`${API_V1}/ota/releases`, { method: 'POST', body: form, raw: true }),
  deleteRelease: (version) => send(`${API_V1}/ota/releases/${encodeURIComponent(version)}`, del()),
  rearmRelease: (version, deviceId) =>
    send(`${API_V1}/ota/releases/${encodeURIComponent(version)}/rearm/${encodeURIComponent(deviceId)}`, post()),

  // ── 审计 ──（admin）
  /** 审计三类共用这一条：`kind` 取 `calls` / `commands` / `background-task-runs`。 */
  audit: (kind, params) => send(`${API_V1}/audit/${kind}${query(params)}`),

  // ── 后台任务 ──（admin）
  /** 当前态：四任务的开关与名单、延长中的设备、调度器的即时计划。只读组合。 */
  backgroundTasksStatus: () => send(`${API_V1}/background-tasks/status`),
}
