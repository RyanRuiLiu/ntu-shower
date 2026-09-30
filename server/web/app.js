/**
 * 应用核心：API 封装、实时推送、视图切换、共享状态。
 *
 * 合并自旧系统的两个网页端（shower-server 的调试面板、bridge-server 的画面
 * 查看器）。两者原本各自维护一份 WebSocket 与状态，合并后这里只连一条。
 */

// ── API ───────────────────────────────────────────────────
const API = '/api/v1'

/** 服务端的 RFC 9457 问题详情。`title`/`detail` 已是可直接展示的中文。 */
async function request(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!res.ok) {
    // 服务端保证错误体是 problem+json；解析失败时退回状态码，不吞掉失败
    const problem = await res.json().catch(() => null)
    const message = problem?.detail || problem?.title || `HTTP ${res.status}`
    throw new Error(message)
  }

  return res.status === 204 ? null : res.json()
}

export const api = {
  /** 全部喷头现态。查看类接口，无需身份。 */
  doors: () => request('/doors'),
  assign: (body) => request('/sessions/assign', { method: 'POST', body: JSON.stringify(body) }),
  settle: (body) => request('/sessions/settle', { method: 'POST', body: JSON.stringify(body) }),
  photo: (body) => request('/photos', { method: 'POST', body: JSON.stringify(body) }),
}

// ── 状态 ──────────────────────────────────────────────────
/**
 * 共享状态。两个视图都从这里读，避免各自维护一份。
 *
 * `doors` 是当前全量现态；WS 只推变化，所以连上后要先 REST 拉一次兜底。
 */
export const store = {
  doors: [],
  deviceId: '',
  online: false,

  setDoors(doors) {
    this.doors = doors
    if (doors.length > 0 && !this.deviceId) {
      this.deviceId = doors[0].device_id
      emit('device', this.deviceId)
    }
    emit('doors', doors)
  },

  setOnline(online) {
    if (this.online === online) return
    this.online = online
    emit('online', online)
  },
}

// 极简事件订阅：两个视图各注册自己关心的
const listeners = new Map()

function emit(event, data) {
  for (const fn of listeners.get(event) ?? []) fn(data)
}

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, [])
  listeners.get(event).push(fn)
}

// ── 实时推送 ──────────────────────────────────────────────
const RECONNECT_MS = 3000
let ws = null
let reconnectTimer = null

function wsUrl() {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${location.host}${API}/stream`
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return

  clearTimeout(reconnectTimer)
  try {
    ws = new WebSocket(wsUrl())
  } catch {
    scheduleReconnect()
    return
  }

  ws.onopen = () => {
    clearTimeout(reconnectTimer)
    emit('ws', true)
    log('o', '实时连接已建立')
  }

  ws.onmessage = (e) => {
    try {
      handle(JSON.parse(e.data))
    } catch (err) {
      log('x', `消息解析失败：${err.message}`)
    }
  }

  ws.onclose = (ev) => {
    emit('ws', false)
    log('x', `实时连接断开（${ev.code}），${RECONNECT_MS / 1000} 秒后重连`)
    scheduleReconnect()
  }

  ws.onerror = () => emit('ws', false)
}

function scheduleReconnect() {
  if (!reconnectTimer) reconnectTimer = setTimeout(connect, RECONNECT_MS)
}

/**
 * 处理服务端推送。
 *
 * 消息形状是 `{ type, payload }`（见 server/app/core/events.py）。
 * 服务端只推变化，所以首次进页面必须先 REST 拉一次全量。
 */
function handle(msg) {
  switch (msg.type) {
    case 'door_update':
      // payload: { device_id, doors, at }
      mergeDoors(msg.payload.device_id, msg.payload.doors)
      log('s', `喷头状态更新（${msg.payload.doors.length} 项）`)
      break

    case 'device_online':
      store.setOnline(true)
      log('o', `设备上线：${msg.payload.device_id}`)
      break

    case 'device_offline':
      store.setOnline(false)
      log('x', `设备离线：${msg.payload.device_id}`)
      break

    default:
      log('i', `未知事件：${msg.type}`)
  }
}

/** 把变化合并进现态，并让对应门牌闪一下。 */
function mergeDoors(deviceId, changed) {
  const merged = [...store.doors]
  for (const door of changed) {
    const idx = merged.findIndex((d) => d.device_id === deviceId && d.door_no === door.door_no)
    if (idx >= 0) {
      merged[idx] = { ...merged[idx], ...door }
      emit('flash', door.door_no)
    } else {
      merged.push(door)
    }
  }
  store.setDoors(merged)
}

// ── 事件日志 ──────────────────────────────────────────────
const MAX_LOG = 200
const logs = []

export function log(level, message) {
  logs.push({ level, message, at: new Date() })
  if (logs.length > MAX_LOG) logs.shift()
  emit('log', logs)
}

export function clearLog() {
  logs.length = 0
  emit('log', logs)
}

export function getLogs() {
  return logs
}

// ── 通用小工具 ────────────────────────────────────────────
export const $ = (id) => document.getElementById(id)

/** 短暂的浮层提示，用于"已复制""已清空"这类不需要确认的操作。 */
export function toast(text) {
  const el = $('toast')
  el.textContent = text
  el.classList.add('show')
  setTimeout(() => el.classList.remove('show'), 1500)
}

/** 时钟：顶部栏显示服务端时间，便于与设备日志对齐。 */
function startClock() {
  const el = $('clock')
  setInterval(() => {
    el.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  }, 1000)
}

// ── 视图切换 ──────────────────────────────────────────────
const VIEWS = ['status', 'control']

function showView(name) {
  for (const v of VIEWS) {
    $(v === 'status' ? 'viewStatus' : 'viewControl').hidden = v !== name
  }
  document.body.dataset.view = name

  for (const btn of document.querySelectorAll('.bar-nav-item')) {
    if (btn.dataset.view === name) {
      btn.setAttribute('aria-current', 'page')
    } else {
      btn.removeAttribute('aria-current')
    }
  }

  // 切走遥控视图时停止画面拉取，避免后台空耗设备
  emit('view', name)
  location.hash = name
}

function initNav() {
  for (const btn of document.querySelectorAll('.bar-nav-item')) {
    btn.addEventListener('click', () => showView(btn.dataset.view))
  }
  const initial = location.hash.slice(1)
  showView(VIEWS.includes(initial) ? initial : 'status')
}

// ── 启动 ──────────────────────────────────────────────────
async function refreshDoors() {
  try {
    store.setDoors(await api.doors())
  } catch (err) {
    log('x', `获取喷头状态失败：${err.message}`)
  }
}

async function main() {
  const { mountStatus } = await import('./status.js')
  const { mountControl } = await import('./control.js')

  mountStatus()
  mountControl()
  initNav()
  startClock()
  connect()

  // WS 只推变化，首次进来先拉一次全量兜底
  await refreshDoors()
  setInterval(refreshDoors, 30_000)
}

main()
