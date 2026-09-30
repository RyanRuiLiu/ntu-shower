/**
 * 应用装配：视图注册表、顶栏、实时通道、路由。
 *
 * ## 目录结构
 *
 * - `core/` —— 基础设施，**每样只有一份**：请求封装、状态、DOM 助手、时间同步、
 *   结果呈现。视图不直接碰 `fetch`，网络访问一律经 `core/api.js`。
 * - `views/` —— 一个能力域一个模块，导出 `{ id, title, mount(root), unmount? }`。
 * - `style/` —— `tokens.css` 是颜色与间距的唯一来源，其余按域分文件。
 *
 * 这里是**唯一**的装配点。加一个视图 = 在 `views/` 下加一个模块 + 在 [VIEWS] 里排
 * 一个位置，别处都不用动。导航按钮由注册表生成，不写死在 HTML 里——两处维护的
 * 导航迟早会对不上。
 *
 * `mount` 在切入时调用，`unmount` 负责停掉自己起的轮询与监听。
 */
import { api, getToken, setToken } from './core/api.js'
import { $, esc, listen } from './core/dom.js'
import { log } from './core/log.js'
import { toast } from './core/toast.js'
import {
  emit,
  refreshDevices,
  selectDevice,
  setDoors,
  setOnline,
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
 * 运行时报错直接进事件日志。
 *
 * 现场用平板或手机打开时没有控制台，报错若不落到页面上，表现只是"某个按钮没反应"，
 * 无从判断是脚本挂了还是后端没回。异步失败（`unhandledrejection`）同样要接——
 * `fetch` 抛出的错误大多走那条路。
 */
window.addEventListener('error', (event) => {
  log('err', `脚本错误：${event.message}`)
  // 同时也弹一下：日志区在概览视图里，而概览本身就可能没渲染出来
  toast(`脚本错误：${event.message}`)
})
window.addEventListener('unhandledrejection', (event) => {
  log('err', `未处理的失败：${event.reason?.message || event.reason}`)
})

/** 导航顺序即此数组顺序。 */
const VIEWS = [
  overviewView,
  inspectorView,
  devicesView,
  configView,
  identitiesView,
  releasesView,
  auditView,
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
  // 用 innerHTML 而不是 replaceChildren：后者是较新的 API，老一些的浏览器上不存在，
  // 一旦抛错整个导航就是空的，表现成"点了没反应"，很难往 API 兼容性上想
  nav.innerHTML = VIEWS.map(
    (view) => `<button type="button" class="bar-nav-item" data-view="${esc(view.id)}">${esc(view.title)}</button>`,
  ).join('')
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

  subscribe('devices', renderDevicePicker)

  subscribe('device', (id) => {
    $('deviceId').textContent = id || '未选择'
    $('devicePicker').value = id
  })

  listen($('devicePicker'), 'change', (event) => selectDevice(event.target.value))

  // 设备相关的接口都要 X-Ops-Token，没它连清单都读不到——所以入口放在顶栏，
  // 而不是某个视图里（那是全局凭证，不是某个功能的设置）
  listen($('btnToken'), 'click', askForToken)

  // 顶部显示服务端时间，便于与设备日志对齐
  setInterval(() => {
    $('clock').textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false })
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
 * 消息形状是 `{ type, payload }`（见 `server/app/core/events.py`）。服务端只推变化，
 * 所以首次进页面必须先 REST 拉一次全量。
 */
function handle(message) {
  switch (message.type) {
    case 'door_update':
      // 推送里只有"哪几扇门变了"（`door_no` / `state` / `card_no`），**不是完整
      // 现态**——分配时间、学号都不在其中。拿它替换列表会让门牌上的明细一夜之间
      // 全变成 `--`，所以收到通知后重新拉一次全量。
      scheduleDoorRefresh()
      break

    case 'device_online':
      setOnline(true)
      log('ok', `设备上线：${message.payload.device_id}`)
      break

    case 'device_offline':
      setOnline(false)
      log('err', `设备离线：${message.payload.device_id}`)
      break

    default:
      log('info', `未知事件：${message.type}`)
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

  socket.onopen = () => {
    clearTimeout(reconnectTimer)
    setStreaming(true)
    log('ok', '实时连接已建立')
  }
  socket.onmessage = (event) => {
    try {
      handle(JSON.parse(event.data))
    } catch {
      log('err', '推送消息解析失败')
    }
  }
  socket.onclose = (event) => {
    setStreaming(false)
    log('err', `实时连接断开（${event.code}），${RECONNECT_MS / 1000} 秒后重连`)
    reconnectTimer = setTimeout(connect, RECONNECT_MS)
  }
  socket.onerror = () => setStreaming(false)
}

// ── 启动 ───────────────────────────────────────────────────

/**
 * 提示填写访问令牌。
 *
 * 顶栏按钮与"收到 401"两条路径共用它。填完立刻重拉设备与门——否则用户填了令牌
 * 还得自己再刷一次页面。
 */
function askForToken() {
  const input = window.prompt('访问令牌（X-Ops-Token）', getToken())
  if (input === null) return
  setToken(input.trim())
  toast(input.trim() ? '令牌已保存' : '令牌已清除')
  refreshDevicesSafely()
  refreshDoors()
}

/**
 * 处理鉴权失败：说明原因并请用户补令牌。
 *
 * **必须集中处理**。此前每个调用方各自 `catch` 后只写进事件日志，而那个面板已经
 * 去掉了——于是 401 完全静默，页面只剩空列表，看起来就是"设备离线、刷新也没用"，
 * 而真正的原因（缺令牌）一个字都看不到。返回 true 表示已处理，调用方不必再记一次。
 */
function handleAuthError(error) {
  if (error?.status !== 401) return false
  toast('访问令牌缺失或无效，请在顶栏填写')
  askForToken()
  return true
}

async function refreshDoors() {
  try {
    setDoors(await api.doors(store.deviceId))
  } catch (error) {
    if (!handleAuthError(error)) log('err', `获取喷头状态失败：${error.message}`)
  }
}

/**
 * 合并短时间内的多次刷新请求。
 *
 * 设备按秒级上报，一屋子喷头同时变动会瞬间推来一串通知。逐条去拉全量既浪费往返，
 * 也会让界面在几百毫秒里反复重排；合并成一次就够了。
 */
let refreshTimer = null

function scheduleDoorRefresh() {
  if (refreshTimer) return
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    refreshDoors()
  }, 800)
}

async function refreshDevicesSafely() {
  try {
    await refreshDevices()
  } catch (error) {
    if (!handleAuthError(error)) log('err', `获取设备清单失败：${error.message}`)
  }
}

async function main() {
  initNav()
  bindTopbar()
  connect()

  await refreshDevicesSafely()
  await refreshDoors()

  // 设备清单与喷头现态各 30 秒兜一次：WS 断线期间也要能纠正在线状态
  setInterval(refreshDevicesSafely, 30_000)
  setInterval(refreshDoors, 30_000)
}

main()
