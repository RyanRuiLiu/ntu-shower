/**
 * 应用装配：视图注册表、顶栏、实时通道、路由。
 *
 * ## 目录结构
 *
 * - `core/` —— 基础设施，每样只有一份：请求封装、状态、DOM 助手、时间同步、
 *   结果呈现。视图不直接碰 `fetch`，网络访问一律经 `core/api.js`。
 * - `views/` —— 一个能力域一个模块，导出 `{ id, title, mount(root), unmount? }`。
 * - `style/` —— `tokens.css` 是颜色与间距的唯一来源，其余按域分文件。
 *
 * 这里是唯一的装配点。加一个视图 = 在 `views/` 下加一个模块 + 在 [VIEWS] 里排
 * 一个位置，别处都不用动。导航按钮由注册表生成，不写死在 HTML 里。
 *
 * `mount` 在切入时调用，`unmount` 负责停掉自己起的轮询与监听。
 */
import { api, getIdentity, hasIdentity, setIdentity } from './core/api.js'
import { fmtDateTime, fmtDuration, fmtServerTimeNow, fmtUptime } from './core/clock.js'
import { EVENT_TYPES } from './core/contract.js'
import { $, esc, listen, PLACEHOLDER } from './core/dom.js'
import { problemText } from './core/outcome.js'
import { toast } from './core/toast.js'
import {
  currentDevice,
  currentRuntime,
  emit,
  refreshDevices,
  refreshRuntime,
  selectDevice,
  upsertDevice,
  setDoors,
  setStreaming,
  store,
  subscribe,
} from './core/store.js'

import { auditView } from './views/audit.js'
import { configView } from './views/config.js'
import { devicesView } from './views/devices.js'
import { identitiesView } from './views/identities.js'
import { inspectorView } from './views/inspector.js'
import { overviewView } from './views/overview.js'
import { releasesView } from './views/releases.js'

/**
 * 运行时报错直接弹提示。
 *
 * 现场用平板或手机打开时没有控制台，报错若不落到页面上，表现只是"某个按钮没反应"。
 * 异步失败（`unhandledrejection`）同样要接：`fetch` 抛出的错误大多走那条路。
 */
window.addEventListener('error', (event) => {
  toast(`页面运行异常：${event.message}`)
})
window.addEventListener('unhandledrejection', (event) => {
  toast(`页面运行异常：${event.reason?.message || event.reason}`)
})

/** 视图注册表：id → 模块。顺序不代表导航顺序，导航见 [NAV_GROUPS]。 */
const VIEWS = [
  overviewView,
  inspectorView,
  devicesView,
  configView,
  identitiesView,
  releasesView,
  auditView,
]

/**
 * 导航分组。数组的每一项是一组，组内相邻渲染，组间画一条分隔。
 *
 * 分的是作用范围，不是功能类别：
 *
 * - 前一组都作用于当前选中的那台设备（它的喷头现状、它的档案与命令、它的界面）；
 * - 后一组是全局的，作用范围跨越所有设备（改配置、管权限、发版本、查流水）。
 *
 * 实际意义在设备选择器上：切了设备，前一组的内容跟着换，后一组不换。
 */
const NAV_GROUPS = [
  ['overview', 'devices', 'inspector'],
  ['config', 'identities', 'releases', 'audit'],
]

let active = null

// ── 路由与视图切换 ─────────────────────────────────────────

function showView(id) {
  const view = VIEWS.find((item) => item.id === id) || VIEWS[0]
  if (active === view) return
  if (active) active.unmount?.()

  active = view
  for (const item of VIEWS) $(`view-${item.id}`).hidden = item.id !== view.id
  for (const btn of document.querySelectorAll('.bar-nav-item')) {
    if (btn.dataset.view === view.id) btn.setAttribute('aria-current', 'page')
    else btn.removeAttribute('aria-current')
  }
  if (location.hash.slice(1) !== view.id) location.hash = view.id
  emit('view', view.id)
  view.mount($(`view-${view.id}`))
}

function initNav() {
  const nav = $('nav')
  const byId = new Map(VIEWS.map((view) => [view.id, view]))

  // 加视图时忘了排进分组，那个页面就永远打不开，而界面上不会有任何提示——
  // 导航里根本没有它。这里让它在启动时就暴露（与 overview 里那张状态文案表同一个做法）。
  const grouped = new Set(NAV_GROUPS.flat())
  const missing = VIEWS.filter((view) => !grouped.has(view.id)).map((view) => view.id)
  if (missing.length > 0) throw new Error(`导航分组表漏了视图：${missing.join('、')}`)

  // 用 innerHTML 而不是 replaceChildren：后者是较新的 API，老一些的浏览器上不存在，
  // 一旦抛错整个导航就是空的，表现成"点了没反应"，很难往接口行为差异上想
  nav.innerHTML = NAV_GROUPS.map((group) => {
    const items = group
      .map((id) => byId.get(id))
      .filter((view) => view !== undefined)
      .map((view) => `<button type="button" class="bar-nav-item" data-view="${esc(view.id)}">${esc(view.title)}</button>`)
      .join('')
    return `<div class="bar-nav-group">${items}</div>`
  }).join('')
  for (const btn of nav.querySelectorAll('.bar-nav-item')) {
    listen(btn, 'click', () => showView(btn.dataset.view))
  }
  const initial = location.hash.slice(1)
  showView(VIEWS.some((item) => item.id === initial) ? initial : VIEWS[0].id)
}

// ── 顶栏 ───────────────────────────────────────────────────

/** 设备选择器：多设备下每个视图都要能切，所以放在顶栏而不是某个视图里。 */
function renderDevicePicker() {
  const select = $('devicePicker')
  select.innerHTML = store.devices
    .map(
      (device) =>
        `<option value="${esc(device.device_id)}">${esc(device.name || device.device_id)}${device.online ? '（在线）' : '（离线）'}</option>`,
    )
    .join('')
  select.value = store.deviceId
  select.disabled = store.devices.length === 0
}

/**
 * 设备信息面板的内容。
 *
 * 顺序即重要程度：先认身份（名称、ID），再看它能不能收命令（在线、信任、跑了多久），
 * 最后是排障要拿去搜日志的那几项（版本、系统、能力、最后上报、来源 IP）。
 *
 * 面板里的每一行都会渲染，缺值给占位符——不按有没有值增减行数，行数忽多忽少会让人
 * 分不清"这一项没有了"与"这一项没值"。
 */
function renderDeviceInfo() {
  const summary = currentDevice()
  const diag = currentRuntime()
  const caps = diag ? diag.capabilities || [] : null
  // 诊断字段（版本、IP、时钟偏差）只在持有管理身份时可得；缺失给占位符，
  // 不隐藏行——行数忽多忽少会让人以为"这一项没有了"
  const rows = summary
    ? [
        ['名称', summary.name || PLACEHOLDER],
        ['设备 ID', summary.device_id],
        ['状态', summary.online ? '在线' : '离线'],
        ['信任', diag ? (diag.trusted ? '已信任' : '未信任') : PLACEHOLDER],
        ['应用版本', diag ? `${diag.app_version}（版本号 ${diag.version_code}）` : PLACEHOLDER],
        ['系统版本', diag ? diag.os_version || PLACEHOLDER : PLACEHOLDER],
        // 空数组是"设备未声明能力集"（升级前的旧版本设备），不是"没有能力"，措辞要分开
        ['能力位', caps ? (caps.length > 0 ? `${caps.length} 项` : '未声明') : PLACEHOLDER],
        ['已运行', diag ? fmtUptime(diag.booted_at) : PLACEHOLDER],
        ['最后上报', diag ? fmtDateTime(diag.last_seen_at) : PLACEHOLDER],
        ['来源 IP', diag ? diag.last_ip || PLACEHOLDER : PLACEHOLDER],
      ]
    : [['设备', '未选择']]

  $('deviceInfo').innerHTML = rows
    .map(([key, value]) => {
      // "已运行"要每秒重算，标出来给 tickDeviceInfo 更新；其余几行是快照，渲染一次就够
      const live = key === '已运行' && diag ? ' data-uptime' : ''
      // 只报数量不够排障："命令被 device.unsupported_action 拒绝"时要对着设备声明逐项核对。
      // 动作名照原样显示，与审计页同一原则——翻译成中文反而搜不到
      const capsHtml =
        key === '能力位' && caps && caps.length > 0
          ? `<div class="info-caps">${caps.map((cap) => `<span class="badge">${esc(cap)}</span>`).join('')}</div>`
          : ''
      return `<div class="kv"><span class="k">${esc(key)}</span><span class="v"${live}>${esc(value)}</span></div>${capsHtml}`
    })
    .join('')
}

/**
 * 面板里那些"会自己变"的字段。
 *
 * 挂在顶栏原有的每秒定时器上，不另起一个：面板是点开扫一眼的东西，为它单独维护一个
 * 定时器的生命周期（开时启动、关时停掉、视图切换时还不能漏）不划算。
 */
function tickDeviceInfo() {
  const panel = $('deviceInfo')
  if (panel.hidden) return
  const cell = panel.querySelector('[data-uptime]')
  const diag = currentRuntime()
  if (cell && diag) cell.textContent = fmtUptime(diag.booted_at)
}

/** 开合设备信息面板。按钮的 `aria-expanded` 跟着走，读屏才知道它是收起的还是展开的。 */
function toggleDeviceInfo(open) {
  const panel = $('deviceInfo')
  const btn = $('btnDeviceInfo')
  const next = open ?? panel.hidden
  if (next) renderDeviceInfo()
  panel.hidden = !next
  btn.setAttribute('aria-expanded', String(next))
}

function bindTopbar() {
  subscribe('online', (online) => {
    $('deviceDot').className = `bar-dot ${online ? 'on' : 'off'}`
    $('deviceText').textContent = online ? '设备在线' : '设备离线'
  })

  subscribe('streaming', (streaming) => {
    const chip = $('streamChip')
    chip.className = `badge ${streaming ? 'badge-green' : 'badge-red'}`
    chip.textContent = streaming ? '实时' : '断开'
  })

  subscribe('devices', () => {
    renderDevicePicker()
    // 面板开着时设备清单变了（改名、上下线），内容要跟着变，否则看的是旧的
    if (!$('deviceInfo').hidden) renderDeviceInfo()
  })

  subscribe('runtime', () => {
    // 运维档案到达（或因身份缺失清空）时刷新诊断面板
    if (!$('deviceInfo').hidden) renderDeviceInfo()
  })

  subscribe('device', (id) => {
    $('devicePicker').value = id
    if (!$('deviceInfo').hidden) renderDeviceInfo()
  })

  listen($('devicePicker'), 'change', (event) => selectDevice(event.target.value))

  listen($('btnDeviceInfo'), 'click', (event) => {
    // 不冒泡到 document，否则紧接着的"点外部关闭"会立刻把它收回去
    event.stopPropagation()
    toggleDeviceInfo()
  })

  listen($('deviceInfo'), 'click', (event) => event.stopPropagation())
  listen(document, 'click', () => toggleDeviceInfo(false))
  listen(document, 'keydown', (event) => {
    if (event.key === 'Escape') toggleDeviceInfo(false)
  })

  // 管理面接口都要 X-Identity（admin 能力位的身份），没它连清单都读不到——所以
  // 入口放在顶栏，而不是某个视图里（那是全局凭证，不是某个功能的设置）
  listen($('btnIdentity'), 'click', askForIdentity)

  // 顶部显示服务端时间，便于与设备日志对齐
  setInterval(() => {
    $('clock').textContent = fmtServerTimeNow()
    tickDeviceInfo()
  }, 1000)
}

// ── 实时通道 ───────────────────────────────────────────────

const RECONNECT_MS = 3000
let socket = null
let reconnectTimer = null

function streamUrl() {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${location.host}/api/v1/stream`
}

/**
 * 处理服务端推送。
 *
 * 消息形状是 `{ type, payload }`（见 `server/app/core/events.py`）。
 */
function handle(message) {
  switch (message.type) {
    case EVENT_TYPES.DOOR_UPDATE:
      // 载荷是该设备的完整现态行（与 GET /doors 同构）：是当前选中设备的就直接
      // 整体替换，不回拉。不是当前设备的（管理台可能多开设备视图）忽略。
      if (message.payload.device_id === store.deviceId) setDoors(message.payload.doors)
      break

    case EVENT_TYPES.DEVICE_UPDATE:
      // payload 是精简档案（名称、在线），两份设备状态里属于该设备的那条同步更新。
      upsertDevice(message.payload)
      break

    default:
      // 未知事件不处理：服务端可能比本页新，这是协议的前向扩展点
      break
  }
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return
  clearTimeout(reconnectTimer)
  try {
    socket = new WebSocket(streamUrl())
  } catch {
    reconnectTimer = setTimeout(connect, RECONNECT_MS)
    return
  }

  let everConnected = false

  socket.onopen = () => {
    clearTimeout(reconnectTimer)
    setStreaming(true)
    // 断线期间错过的通知无法补发（通知不含状态）：按"通知+重拉"模型的标准
    // 行为，重连成功的第一个动作是重拉权威快照；首次连接由 main() 负责，不重复
    if (everConnected) {
      refreshDevicesSafely()
      refreshDoors()
    }
    everConnected = true
  }
  socket.onmessage = (event) => {
    try {
      handle(JSON.parse(event.data))
    } catch (error) {
      // 解析失败意味着协议对不上了，不是能忽略的小事——弹出来，别让它静默
      toast(`推送消息解析失败：${error.message}`)
    }
  }
  socket.onclose = (event) => {
    setStreaming(false)
    reconnectTimer = setTimeout(connect, RECONNECT_MS)
  }
  socket.onerror = () => setStreaming(false)
}

// ── 启动 ───────────────────────────────────────────────────

/**
 * 提示填写身份标识。
 *
 * 顶栏按钮与"收到 401"两条路径共用它。填完立刻重拉设备与喷头——否则用户填了标识
 * 还得自己再刷一次页面。
 */
function askForIdentity() {
  const input = window.prompt('身份标识（X-Identity，须具备管理能力位）', getIdentity())
  if (input === null) return
  setIdentity(input.trim())
  toast(input.trim() ? '身份标识已保存' : '身份标识已清除')
  refreshDevicesSafely()
  refreshRuntimeSafely()
  refreshDoors()
}

/**
 * 处理鉴权失败：说明原因并请用户补身份标识。
 *
 * 集中处理：401 若按普通失败只留下一个空列表，真正的原因（缺身份）一个字都看不到。
 * 返回 true 表示已处理，调用方不必再自己提示。
 */
function handleAuthError(error) {
  if (error?.status !== 401) return false
  toast('身份缺失或未授权，请在顶栏填写身份标识')
  askForIdentity()
  return true
}

/**
 * 刷新运维档案（admin）。仅在已填身份时发起；未填身份不打扰——管理数据
 * 的加载失败不该打断公开视图，也不该一开页就弹输入框。
 */
async function refreshRuntimeSafely() {
  if (!hasIdentity()) return
  try {
    await refreshRuntime()
  } catch (error) {
    if (!handleAuthError(error)) toast(`获取运维档案 · ${problemText(error)}`)
  }
}

async function refreshDoors() {
  try {
    setDoors(await api.doors(store.deviceId))
  } catch (error) {
    // 401 由 handleAuthError 引导补身份标识；其余错误直接弹出来，不留一个无声的空列表
    if (!handleAuthError(error)) toast(`获取喷头状态 · ${problemText(error)}`)
  }
}

async function refreshDevicesSafely() {
  try {
    await refreshDevices()
  } catch (error) {
    toast(`获取设备清单 · ${problemText(error)}`)
  }
}

async function main() {
  initNav()
  bindTopbar()
  connect()

  // 公开数据（设备精简清单、喷头现态）无需身份，始终加载；
  // 运维档案仅在已填管理身份时加载，缺失不打扰。
  // 持续更新全部由推送完成（door.update 数据承载、device.update 在场变化、
  // 重连即重拉），不再有定期轮询。
  await refreshDevicesSafely()
  await refreshDoors()
  await refreshRuntimeSafely()
}

main()
