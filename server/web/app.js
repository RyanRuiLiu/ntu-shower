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
import { fmtDateTime, fmtDuration, fmtServerTimeNow, fmtUptime } from './core/clock.js'
import { EVENT_TYPES } from './core/contract.js'
import { $, esc, listen, PLACEHOLDER } from './core/dom.js'
import { problemText } from './core/outcome.js'
import { toast } from './core/toast.js'
import {
  currentDevice,
  emit,
  refreshDevices,
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
 * 现场用平板或手机打开时没有控制台，报错若不落到页面上，表现只是"某个按钮没反应"，
 * 无从判断是脚本挂了还是后端没回。异步失败（`unhandledrejection`）同样要接——
 * `fetch` 抛出的错误大多走那条路，只记进日志等于没有提示（日志区早已不存在）。
 */
window.addEventListener('error', (event) => {
  toast(`脚本错误：${event.message}`)
})
window.addEventListener('unhandledrejection', (event) => {
  toast(`未处理的失败：${event.reason?.message || event.reason}`)
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
 * 分的是**作用范围**，不是功能类别：
 *
 * - 前一组都作用于**当前选中的那台设备**（它的喷头现状、它的档案与命令、它的界面）；
 * - 后一组是**全局的**，作用范围跨越所有设备（改配置、管权限、发版本、查流水）。
 *
 * 这么分的实际意义在设备选择器上：切了设备，前一组的内容跟着换，后一组不换。七个按钮
 * 等宽平铺时看不出这条界线，而"我接下来点的东西是不是针对这台设备"，是现场最常要判断的。
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
 * 面板里的每一行都会渲染，缺值给占位符——不按有没有值增减行数。行数忽多忽少会让人
 * 以为"这一项没有了"，而实际情况是"这一项没值"，两者要分得开。
 */
/**
 * 设备时钟偏差的说明文案。
 *
 * 存的是"服务端时刻减设备时刻"，所以**负值表示设备快**——那个符号方向反直觉，界面上
 * 必须把它翻成一句能直接读的话，而不是把数字摊出来。
 *
 * 秒级以内算已对齐：校正本身的目标就是这个量级（设备 date 只精确到秒，加上传输延迟，
 * 实测残差约 0.6 秒）。
 */
function skewText(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return PLACEHOLDER
  if (Math.abs(seconds) < 1) return '已对齐'
  const magnitude = fmtDuration(Math.abs(seconds))
  return seconds < 0 ? `快 ${magnitude}` : `慢 ${magnitude}`
}

function renderDeviceInfo() {
  const device = currentDevice()
  const rows = device
    ? [
        ['名称', device.name || PLACEHOLDER],
        ['设备 ID', device.device_id],
        ['状态', device.online ? '在线' : '离线'],
        ['信任', device.trusted ? '已信任' : '未信任'],
        ['应用版本', `${device.app_version}（版本号 ${device.version_code}）`],
        ['系统版本', device.os_version || PLACEHOLDER],
        ['能力位', `${(device.capabilities || []).length} 项`],
        ['已运行', fmtUptime(device.booted_at)],
        ['时钟偏差', skewText(device.clock_skew_s)],
        ['最后上报', fmtDateTime(device.last_seen_at)],
        ['来源 IP', device.last_ip || PLACEHOLDER],
      ]
    : [['设备', '未选择']]

  $('deviceInfo').innerHTML = rows
    .map(([key, value]) => {
      // "已运行"要每秒重算，标出来给 tickDeviceInfo 更新；其余几行是快照，渲染一次就够
      const live = key === '已运行' ? ' data-uptime' : ''
      return `<div class="kv"><span class="k">${esc(key)}</span><span class="v"${live}>${esc(value)}</span></div>`
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
  const device = currentDevice()
  if (cell && device) cell.textContent = fmtUptime(device.booted_at)
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
    // 面板开着时设备清单变了（改名、信任位、上下线），内容要跟着变，否则看的是旧的
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

  // 设备相关的接口都要 X-Ops-Token，没它连清单都读不到——所以入口放在顶栏，
  // 而不是某个视图里（那是全局凭证，不是某个功能的设置）
  listen($('btnToken'), 'click', askForToken)

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
 * 消息形状是 `{ type, payload }`（见 `server/app/core/events.py`）。服务端只推变化，
 * 所以首次进页面必须先 REST 拉一次全量。
 */
function handle(message) {
  switch (message.type) {
    case EVENT_TYPES.DOOR_UPDATE:
      // 推送里只有"哪几个喷头变了"（`door_no` / `state` / `card_no`），**不是完整
      // 现态**——分配时间、学号都不在其中。拿它替换列表会让喷头卡片上的明细一夜之间
      // 全变成占位符，所以收到通知后重新拉一次全量。
      scheduleDoorRefresh()
      break

    case EVENT_TYPES.DEVICE_UPDATE:
      // payload 是**完整的设备记录**，直接替换本地那一条。上线、下线、改名、调整信任
      // 位都走这一个事件——它们是同一个问题（这台设备的档案变了）的四种情形，拆成多个
      // 事件名只会让这里多几个做同样事情的分支。
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

  socket.onopen = () => {
    clearTimeout(reconnectTimer)
    setStreaming(true)
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
 * 提示填写访问令牌。
 *
 * 顶栏按钮与"收到 401"两条路径共用它。填完立刻重拉设备与喷头——否则用户填了令牌
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
 * **必须集中处理**。此前每个调用方各自 `catch`，而 401 与其它失败一样只留下一个空列表
 * ——看起来就是"设备离线、刷新也没用"，真正的原因（缺令牌）一个字都看不到。
 * 返回 true 表示已处理，调用方不必再自己提示。
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
    // 401 由 handleAuthError 引导补令牌；其余错误此前只进日志，而日志区已不存在，
    // 于是页面只剩一个空列表、看起来跟"设备离线"一样——现在弹出来
    if (!handleAuthError(error)) toast(`获取喷头状态 · ${problemText(error)}`)
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
    if (!handleAuthError(error)) toast(`获取设备清单 · ${problemText(error)}`)
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
