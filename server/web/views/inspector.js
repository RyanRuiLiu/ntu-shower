/**
 * 界面检视：看设备当前界面，并对它做原始注入。
 *
 * ## 布局与交互照旧调试页
 *
 * 结构取自 `ntu-remote-shower` 的 `bridge-server/src/viewer.html`：顶部工具条，
 * 下面左右分栏，右列自上而下是节点树、属性面板、输入栏。样式在 `style/inspector.css`。
 *
 * 工具条只留三样：截图、界面树、四个按键。旧版还有方向键与 Enter/Del，我们**不放**——
 * 设备端的 `UiKey` 只有 back / home / recents / notifications 四个，放了就是装饰。
 *
 * ## 鼠标三种手势（照旧版）
 *
 * | 手势 | 结果 |
 * |---|---|
 * | 左键按一下就松 | 点击该点 |
 * | 左键按住不放（超过 [TAP_MAX_MS]）再松 | **长按**，时长按实际按住时间 |
 * | 左键按住拖动到别处再松 | **滑动**，时长同样按实际按住时间 |
 * | **右键** | **取消**当前手势，什么都不发 |
 *
 * 时长一律照实报，不写死：按住三秒就发三秒。按住期间画面上的圈会走满、并实时显示
 * 已按秒数——不然只能凭感觉，不知道这一下会被当成点击还是长按。
 *
 * 长按**不需要新协议**：设备端是"有 `to_x`/`to_y` 走滑动、否则走点击"，所以发
 * `{x, y, to_x: x, to_y: y, duration_ms: 按住时长}`（起点等于终点的滑动）就是长按。
 * 这条路径复用 `ui.touch` 的既有能力，双端代码都不用动。
 *
 * ## 每次操作后自动重截、重读（各自可关）
 *
 * 照旧版：点击 / 长按 / 滑动 / 按键 / 输入之后都自动刷新画面与树，两者并行发出。
 * 不刷的话，点完看到的还是旧画面，得自己再点一次截图才知道点没点中。工具条上给两个
 * 开关（自动画面 / 自动UI树），各自记在 localStorage——有人只想手动截图省掉每步 3 秒
 * 的采集，全写死就堵死了这条路。
 * 两条打磨：截图在飞时不重发（screenrecord 一次约 3 秒且占住采集通道，连点会把手势
 * 挤掉），期间到来的刷新并成一次尾随重取；命令 `failed`/`rejected`（没落到界面上，
 * 画面不会变）不重取，省一轮采集——`timeout` 结果未知，照旧重取。
 *
 * ## 画面与结构都不轮询
 *
 * 截图走 `screenrecord`，一次约 3 秒且占住设备端采集通道。定时拉取会让设备一直在
 * 编码，也把别人的操作挤掉。
 */
import { api } from '../core/api.js'
import { problemText, statusLabel } from '../core/outcome.js'
import { $, disposer, esc, listen } from '../core/dom.js'
import { store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

/**
 * 按住多久算长按而不是单击。
 *
 * 取 Android 系统判长按的标准值（`ViewConfiguration.getLongPressTimeout()`）——设备上的
 * 控件就是按这条线判断的，比它更短会被当成点击。它只决定**算哪种手势**，不决定发给
 * 设备的时长：那个照实报，按住多久发多久。
 */
const TAP_MAX_MS = 500
/** 移动超过这个像素就不再算点击，转为滑动。 */
const DRAG_THRESHOLD = 5
/** 滑动距离小于这个值不算数（只是手抖）。 */
const SWIPE_MIN = 8

/** 节点列表（深度优先摊平，每项带 depth）。 */
let nodes = []
/**
 * 操作后要不要自动刷新画面 / UI 树。记在 localStorage，缺席视为开——
 * 这两步照旧版默认都做，开关只是给想省采集的人留的。
 */
const AUTO_CAPTURE_KEY = 'ntu-shower.inspector.auto-capture'
const AUTO_TREE_KEY = 'ntu-shower.inspector.auto-tree'
let autoCapture = localStorage.getItem(AUTO_CAPTURE_KEY) !== '0'
let autoTree = localStorage.getItem(AUTO_TREE_KEY) !== '0'
/** 父索引 → 子索引数组；根节点的父记作 -1。 */
let children = new Map()
/** 折叠起来的节点索引。 */
let collapsed = new Set()
/** 当前选中的节点索引；-1 表示未选。 */
let selected = -1
/** 最近一次读到的原始 XML，过滤时重渲染用。 */
let currentXml = ''
/** 设备分辨率，取自图片原始像素——坐标换算的基准。 */
let deviceWidth = 0
let deviceHeight = 0

/** 左键按下的起点（设备坐标 + 视口坐标）。 */
let pressStart = null
/** 按下的时刻（`performance.now()`），手势时长按实际按住时间算。 */
let pressStartedAt = 0
/** 计时刷新句柄。 */
let pressTicker = null
/** 拖动起点（设备坐标）；有值时表示正在拖动。 */
let dragStart = null

const BOUNDS = /^\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]$/

function boundsOf(node) {
  const match = BOUNDS.exec(node.bounds || '')
  if (!match) return null
  const [, left, top, right, bottom] = match.map(Number)
  return { left, top, right, bottom, cx: Math.round((left + right) / 2), cy: Math.round((top + bottom) / 2) }
}

/** 类名的末段：`android.widget.LinearLayout` → `LinearLayout`。 */
function shortClass(name) {
  return (name || '').split('.').pop() || '(node)'
}

/**
 * 是否是输入框。
 *
 * 官方 `uiautomator` XML 没有 `editable` 字段（旧版自定义 JSON 有），只能按类名判断。
 * `EditText` 覆盖绝大多数输入场景，`AutoCompleteTextView` 是它的子类，一并算上。
 */
function isEditable(cls) {
  return /EditText|AutoCompleteTextView/.test(cls || '')
}

function setOpState(text, kind) {
  const box = $('opState')
  box.className = kind === 'ok' ? 'tb-status is-ok' : kind === 'err' ? 'tb-status is-err' : 'tb-status'
  box.textContent = text
}

// ── 树 ─────────────────────────────────────────────────────

function parseTree(xml) {
  const doc = new DOMParser().parseFromString(xml, 'text/xml')
  if (doc.querySelector('parsererror')) throw new Error('节点树解析失败')

  const list = []
  const stack = []

  const walk = (element, depth) => {
    const parent = stack.length > 0 ? stack[stack.length - 1] : -1
    list.push({
      index: list.length,
      depth,
      parent,
      text: element.getAttribute('text') || '',
      id: element.getAttribute('resource-id') || '',
      cls: element.getAttribute('class') || '',
      desc: element.getAttribute('content-desc') || '',
      bounds: element.getAttribute('bounds') || '',
      clickable: element.getAttribute('clickable') === 'true',
      longClickable: element.getAttribute('long-clickable') === 'true',
      scrollable: element.getAttribute('scrollable') === 'true',
      checkable: element.getAttribute('checkable') === 'true',
      checked: element.getAttribute('checked') === 'true',
      editable: isEditable(element.getAttribute('class')),
    })
    stack.push(list.length - 1)
    for (const child of element.children) walk(child, depth + 1)
    stack.pop()
  }

  for (const child of doc.documentElement.children) walk(child, 0)

  nodes = list
  children = new Map()
  for (const node of list) {
    if (!children.has(node.parent)) children.set(node.parent, [])
    children.get(node.parent).push(node.index)
  }
}

/** 搜索词支持 `id:` / `text:` / `class:` 前缀，与旧调试页一致。 */
function parseSearch(raw) {
  const text = (raw || '').trim()
  if (text.startsWith('id:') || text.startsWith('res:')) return { field: 'id', term: text.slice(3).trim().toLowerCase() }
  if (text.startsWith('text:')) return { field: 'text', term: text.slice(5).trim().toLowerCase() }
  if (text.startsWith('class:')) return { field: 'cls', term: text.slice(6).trim().toLowerCase() }
  return { field: 'any', term: text.toLowerCase() }
}

function matches(node, search) {
  if (!search.term) return true
  const haystack =
    search.field === 'any'
      ? `${node.cls} ${node.text} ${node.desc} ${node.id}`.toLowerCase()
      : String(node[search.field] || '').toLowerCase()
  return haystack.includes(search.term)
}

function renderTree() {
  const search = parseSearch($('treeSearch').value)
  const hit = new Set(nodes.filter((node) => matches(node, search)).map((node) => node.index))
  const filtering = Boolean(search.term)

  const lines = []
  const walk = (index) => {
    const node = nodes[index]
    if (!node) return
    const kids = children.get(index) || []
    const isCollapsed = collapsed.has(index)

    const classes = ['node']
    if (index === selected) classes.push('is-selected')
    if (node.clickable) classes.push('is-clickable')
    if (node.longClickable) classes.push('is-long-clickable')
    if (node.editable) classes.push('is-editable')
    if (node.scrollable) classes.push('is-scrollable')
    if (filtering && !hit.has(index)) classes.push('is-dim')
    if (filtering && hit.has(index)) classes.push('is-hit')

    const tags = []
    if (node.clickable) tags.push('<span class="node-tag int">tap</span>')
    if (node.longClickable) tags.push('<span class="node-tag long">hold</span>')
    if (node.editable) tags.push('<span class="node-tag edit">edit</span>')
    if (node.scrollable) tags.push('<span class="node-tag scroll">scroll</span>')

    const label = [
      `<span class="node-class">${esc(shortClass(node.cls))}</span>`,
      node.text ? ` <span class="node-text">"${esc(node.text)}"</span>` : '',
      node.id ? ` <span class="node-id">${esc(node.id.split('/').pop())}</span>` : '',
      tags.join(' '),
    ].join('')

    lines.push(
      `<div class="${classes.join(' ')}" data-index="${index}" style="padding-left:${node.depth * 12 + 4}px">
        <span class="node-toggle" data-toggle="${index}">${kids.length > 0 ? (isCollapsed ? '▶' : '▼') : '·'}</span>
        <span class="node-label">${label}</span>
      </div>`,
    )
    if (!isCollapsed) for (const kid of kids) walk(kid)
  }
  for (const root of children.get(-1) || []) walk(root)

  $('treeContent').innerHTML = lines.join('') || '<div class="empty">暂无节点</div>'
  $('treeCount').textContent = filtering ? `${hit.size} / ${nodes.length}` : `${nodes.length} nodes`
}

// ── 选中、面包屑、高亮 ─────────────────────────────────────

function renderBreadcrumb(index) {
  const trail = []
  let cursor = index
  while (cursor >= 0 && trail.length < 24) {
    const node = nodes[cursor]
    if (!node) break
    trail.unshift({ index: cursor, label: node.id ? node.id.split('/').pop() : shortClass(node.cls) })
    cursor = node.parent
  }
  $('breadcrumb').innerHTML = trail
    .map(({ index: at, label }) => `<span data-jump="${at}">${esc(label)}</span>`)
    .join('<span class="sep">›</span>')
}

function renderProps(node) {
  if (!node) {
    for (const id of ['propClass', 'propRid', 'propText', 'propDesc', 'propBounds', 'propSize', 'propCoords']) {
      $(id).textContent = '-'
    }
    $('propTags').innerHTML = ''
    return
  }
  const box = boundsOf(node)
  $('propClass').textContent = node.cls || '-'
  $('propRid').textContent = node.id || '-'
  $('propText').textContent = node.text || '-'
  $('propDesc').textContent = node.desc || '-'
  $('propBounds').textContent = node.bounds || '-'
  $('propSize').textContent = box ? `${box.right - box.left} × ${box.bottom - box.top}` : '-'
  $('propCoords').textContent = box ? `${box.cx}, ${box.cy}` : '-'

  const flags = [
    ['clickable', node.clickable],
    ['long-clickable', node.longClickable],
    ['scrollable', node.scrollable],
    ['checkable', node.checkable],
    ['checked', node.checked],
    ['editable', node.editable],
  ]
  $('propTags').innerHTML = flags
    .map(([name, on]) => `<span class="prop-tag ${on ? 'on' : ''}">${esc(name)}</span>`)
    .join('')
}

/** 在画面上框出选中节点。覆盖层与图片同尺寸，因此按设备坐标的百分比定位。 */
function highlight(index) {
  const node = index >= 0 ? nodes[index] : null
  const box = node ? boundsOf(node) : null
  if (!box || !deviceWidth || !deviceHeight) {
    $('overlay').innerHTML = ''
    return
  }
  const left = (box.left / deviceWidth) * 100
  const top = (box.top / deviceHeight) * 100
  const width = ((box.right - box.left) / deviceWidth) * 100
  const height = ((box.bottom - box.top) / deviceHeight) * 100
  $('overlay').innerHTML = `<div class="highlight" style="left:${left}%;top:${top}%;width:${width}%;height:${height}%"></div>`
}

function selectNode(index) {
  selected = index
  const node = index >= 0 ? nodes[index] : null
  renderProps(node)
  highlight(index)
  if (node) renderBreadcrumb(index)
  else $('breadcrumb').innerHTML = ''
  $('propTap').disabled = !node || !boundsOf(node)
  renderTree()
}

// ── 动作 ───────────────────────────────────────────────────

async function requireDevice() {
  if (!store.deviceId) {
    setOpState('请先在顶栏选择设备', 'err')
    return null
  }
  return store.deviceId
}

async function act(label, run, options = {}) {
  const deviceId = await requireDevice()
  if (!deviceId) return
  setOpState(`${label}…`)
  try {
    const result = await run(deviceId)
    const ok = result.status === 'ok'
    const detail = result.terminal_message || result.reason_code || ''
    // 状态词取自 core.outcome，不自建：这里原先自己拼了"完成"/"失败："两套词，
    // 而概览页用的是 core 那套（"已完成"），同一个系统两种说法。
    // 加上动作名前缀是因为这块面板会连着点好几个动作，光看"已完成"不知道是哪个。
    const state = statusLabel(result.status)
    setOpState(detail ? `${label} · ${state} · ${detail}` : `${label} · ${state}`, ok ? 'ok' : 'err')
    // 操作改变了界面，画面与树都要跟着更新，否则看的是操作前的样子。
    // failed/rejected 是命令没落到界面上（被拒或执行失败），画面不会变，重取只是白花
    // 一轮 3 秒的采集；timeout 的结果未知（命令可能迟达生效），照旧重取。
    if (options.refresh !== false && (ok || result.status === 'timeout')) refreshBoth()
  } catch (error) {
    setOpState(problemText(error), 'err')
  }
}

/** 截图在飞标记：screenrecord 占住设备端采集通道，重复触发只会有害。 */
let capturing = false
/** 刷新单飞与尾随合并：连续操作只补一轮，不把采集通道排成串。 */
let refreshInFlight = false
let refreshPending = false

async function capture() {
  const deviceId = await requireDevice()
  if (!deviceId) return
  if (capturing) return
  capturing = true
  setOpState('正在截图…')
  $('screenPanel').classList.add('is-loading')
  try {
    const result = await api.capture(deviceId)
    const image = result.data?.image_base64
    if (!image) {
      setOpState('设备未返回画面', 'err')
      return
    }
    const url = `data:image/jpeg;base64,${image}`
    const probe = new Image()
    probe.onload = () => {
      deviceWidth = probe.naturalWidth
      deviceHeight = probe.naturalHeight
      $('screenshot').src = url
      $('screenFrame').hidden = false
      $('screenHint').hidden = true
      highlight(selected)
      if (nodes.length > 0) renderTree()
      setOpState(`画面 ${deviceWidth}×${deviceHeight}`, 'ok')
    }
    probe.onerror = () => setOpState('画面解码失败', 'err')
    probe.src = url
  } catch (error) {
    setOpState(problemText(error), 'err')
  } finally {
    $('screenPanel').classList.remove('is-loading')
    capturing = false
  }
}

async function refreshTree() {
  const deviceId = await requireDevice()
  if (!deviceId) return
  setOpState('正在读取界面结构…')
  try {
    const result = await api.dumpTree(deviceId)
    const xml = result.data?.xml
    if (!xml) {
      setOpState(result.terminal_message || '未能读取界面结构', 'err')
      return
    }
    currentXml = xml
    parseTree(xml)
    collapsed = new Set()
    selectNode(-1)
    setOpState(`${nodes.length} 个节点`, 'ok')
  } catch (error) {
    setOpState(problemText(error), 'err')
  }
}

/** 画面与树一起刷新，照旧版：只刷一边会看到不匹配的两份状态。
 *  两个开关各自决定要不要刷，全关就不刷（用户明确要手动控制）。
 *  在飞时不重发，期间的请求并成一次尾随重取——连点五下只产生两轮采集，不是五轮。 */
async function refreshBoth() {
  if (!autoCapture && !autoTree) return
  if (refreshInFlight) {
    refreshPending = true
    return
  }
  refreshInFlight = true
  try {
    const jobs = [autoCapture ? capture() : null, autoTree ? refreshTree() : null].filter(Boolean)
    await Promise.all(jobs)
  } finally {
    refreshInFlight = false
    if (refreshPending) {
      refreshPending = false
      refreshBoth()
    }
  }
}

/** 画面上的坐标换算。越界时夹到边缘，与旧版一致——拖到画面外也要能结束滑动。 */
function toDevice(clientX, clientY) {
  if ($('screenFrame').hidden || !deviceWidth) return null
  const rect = $('screenshot').getBoundingClientRect()
  if (!rect.width) return null
  const cx = Math.max(0, Math.min(rect.width, clientX - rect.left))
  const cy = Math.max(0, Math.min(rect.height, clientY - rect.top))
  return {
    x: Math.round((cx / rect.width) * deviceWidth),
    y: Math.round((cy / rect.height) * deviceHeight),
    cx,
    cy,
    rect,
  }
}

/**
 * 按下指示：一圈进度 + 实时秒数。
 *
 * 进度环走满一圈代表到了长按的界线，秒数照实显示按了多久——没有它就只能凭感觉，
 * 不知道这一下会被当成点击还是长按、更不知道会发出去多长。
 */
function pressIndicator(at) {
  const size = 46
  const radius = size / 2 - 3
  const circumference = 2 * Math.PI * radius
  const layer = $('overlay')

  const ring = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  ring.setAttribute('class', 'press-ring')
  ring.setAttribute('width', size)
  ring.setAttribute('height', size)
  ring.style.left = `${(at.cx / at.rect.width) * 100}%`
  ring.style.top = `${(at.cy / at.rect.height) * 100}%`
  ring.innerHTML =
    `<circle cx="${size / 2}" cy="${size / 2}" r="${radius}" ` +
    `stroke-dasharray="${circumference}" stroke-dashoffset="${circumference}"/>`

  const label = document.createElement('div')
  label.className = 'press-label'
  label.style.left = `${(at.cx / at.rect.width) * 100}%`
  label.style.top = `${(at.cy / at.rect.height) * 100}%`

  layer.append(ring, label)
  const circle = ring.querySelector('circle')

  return {
    tick(heldMs) {
      circle.setAttribute('stroke-dashoffset', String(circumference * (1 - Math.min(heldMs / TAP_MAX_MS, 1))))
      ring.classList.toggle('is-held', heldMs >= TAP_MAX_MS)
      label.textContent = `${(heldMs / 1000).toFixed(1)}s`
      label.classList.toggle('is-held', heldMs >= TAP_MAX_MS)
    },
    clear() {
      ring.remove()
      label.remove()
    },
  }
}

function stopPress() {
  if (pressTicker !== null) {
    cancelAnimationFrame(pressTicker)
    pressTicker = null
  }
  pressIndicatorRef?.clear()
  pressIndicatorRef = null
  pressStart = null
}

let pressIndicatorRef = null

/** 画一次涟漪。颜色由调用方给：点击绿、长按橙、滑动紫，一眼分得清刚才发的是哪种。 */
function ripple(at, color) {
  const dot = document.createElement('div')
  dot.className = 'ripple'
  dot.style.left = `${(at.cx / at.rect.width) * 100}%`
  dot.style.top = `${(at.cy / at.rect.height) * 100}%`
  dot.style.borderColor = color
  $('overlay').appendChild(dot)
  setTimeout(() => dot.remove(), 500)
}

function tapAt(x, y) {
  act(`点击 ${x},${y}`, (deviceId) => api.touch(deviceId, { x, y }))
}

/**
 * 长按。
 *
 * 起点等于终点的滑动就是长按——设备端按"有没有 `to_x`"分派，所以这条路不需要新的
 * 动作名，也不用改设备端。`durationMs` 照实取按住时间，不写死：按住三秒就该按住三秒，
 * 写死一个值会让"按住多久"变成无从表达的东西，而某些界面确实靠按多久区分行为。
 */
function longPressAt(x, y, durationMs) {
  act(`长按 ${x},${y} ${(durationMs / 1000).toFixed(1)}s`, (deviceId) =>
    api.touch(deviceId, { x, y, to_x: x, to_y: y, duration_ms: durationMs }),
  )
}

/** 滑动。时长同样取实际按住时间——拖得快就该滑得快。 */
function swipeFrom(from, to, durationMs) {
  act(`滑动 ${from.x},${from.y} → ${to.x},${to.y} ${(durationMs / 1000).toFixed(1)}s`, (deviceId) =>
    api.touch(deviceId, { x: from.x, y: from.y, to_x: to.x, to_y: to.y, duration_ms: durationMs }),
  )
}

function tapSelected() {
  const node = nodes[selected]
  const box = node ? boundsOf(node) : null
  if (!box) {
    setOpState('选中的节点没有可用坐标', 'err')
    return
  }
  tapAt(box.cx, box.cy)
}

// ── 拖动的方向箭头 ─────────────────────────────────────────

function drawSwipe(from, to) {
  const layer = $('swipeLayer')
  const angle = Math.atan2(to.cy - from.cy, to.cx - from.cx)
  const head = 12

  const path = (x1, y1, x2, y2, stroke, width, dash = '') =>
    `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${width}" ${dash ? `stroke-dasharray="${dash}"` : ''} stroke-linecap="round"/>`

  layer.innerHTML = `
    ${path(from.cx, from.cy, to.cx, to.cy, 'var(--purple)', 3, '8,4')}
    <polygon points="${to.cx},${to.cy} ${to.cx - head * Math.cos(angle - 0.45)},${to.cy - head * Math.sin(angle - 0.45)} ${to.cx - head * Math.cos(angle + 0.45)},${to.cy - head * Math.sin(angle + 0.45)}" fill="var(--purple)"/>
    <circle cx="${from.cx}" cy="${from.cy}" r="5" fill="var(--purple)"/>
    <circle cx="${to.cx}" cy="${to.cy}" r="4" fill="var(--text)"/>`
}

function clearSwipe() {
  $('swipeLayer').innerHTML = ''
}

// ── 右键菜单 ───────────────────────────────────────────────

let ctxIndex = -1

function showContextMenu(clientX, clientY, index) {
  ctxIndex = index
  const menu = $('ctxMenu')
  menu.hidden = false
  menu.style.left = `${Math.min(clientX, window.innerWidth - 190)}px`
  menu.style.top = `${Math.min(clientY, window.innerHeight - 90)}px`
}

function hideContextMenu() {
  $('ctxMenu').hidden = true
}

function copy(text, what) {
  navigator.clipboard.writeText(text).then(
    () => toast(`已复制${what}`),
    () => setOpState('复制失败（浏览器未授予剪贴板权限）', 'err'),
  )
}

// ── 视图 ───────────────────────────────────────────────────

let dispose = disposer()

export const inspectorView = {
  id: 'inspector',
  title: '界面检视',

  mount(root) {
    root.innerHTML = `
      <div class="tb">
        <button type="button" class="primary" data-act="capture">Screenshot</button>
        <button type="button" data-act="tree">UI Tree</button>
        <span class="sep"></span>
        <span class="grp">
          <button type="button" data-key="home">Home</button>
          <button type="button" data-key="back">Back</button>
          <button type="button" data-key="recents">Recent</button>
          <button type="button" data-key="notifications">Notify</button>
        </span>
        <div class="tb-right">
          <label class="tb-toggle" title="操作后自动重截画面">
            <input type="checkbox" id="autoCaptureToggle"> 自动画面
          </label>
          <label class="tb-toggle" title="操作后自动重读界面结构">
            <input type="checkbox" id="autoTreeToggle"> 自动UI树
          </label>
          <span class="tb-status" id="opState">就绪</span>
        </div>
      </div>

      <div class="stage">
        <div class="screen-panel" id="screenPanel">
          <span class="muted" id="screenHint">点「Screenshot」加载画面，之后可直接在画面上操作</span>
          <div class="screen-frame" id="screenFrame" hidden>
            <img id="screenshot" alt="设备屏幕" draggable="false">
            <div class="overlay" id="overlay"></div>
            <svg class="swipe-layer" id="swipeLayer"></svg>
            <svg class="crosshair" id="crosshair">
              <line id="crossH" x1="0" y1="0" x2="100%" y2="0"></line>
              <line id="crossV" x1="0" y1="0" x2="0" y2="100%"></line>
            </svg>
            <!-- 提示条必须挂在 screen-frame 里：坐标按图片算，挂在面板上会差出留白，
                 而面板 overflow:hidden 会把贴边时的提示条裁掉 -->
            <div class="coord-tip" id="coordTip" hidden></div>
          </div>
        </div>

        <div class="tree-panel">
          <div class="panel-header">
            UI Tree
            <span class="count" id="treeCount"></span>
            <span class="actions">
              <button type="button" data-act="collapse" title="折叠选中节点的子树">[-]</button>
              <button type="button" data-act="expand" title="展开全部">[+]</button>
            </span>
          </div>
          <input type="text" class="tree-search" id="treeSearch" placeholder="搜索  class:Button  text:确认  id:btn  …">
          <div class="breadcrumb" id="breadcrumb"></div>
          <div class="tree-content" id="treeContent"><div class="empty">点击「UI Tree」加载</div></div>

          <div class="prop-panel">
            <div class="prop-header">属性</div>
            <div class="prop-section">
              <div class="prop-section-title">基本信息</div>
              <div class="prop-row"><span class="prop-key">Class</span><span class="prop-val" id="propClass">-</span></div>
              <div class="prop-row"><span class="prop-key">ResId</span><span class="prop-val" id="propRid">-</span></div>
              <div class="prop-row"><span class="prop-key">Text</span><span class="prop-val" id="propText">-</span></div>
              <div class="prop-row"><span class="prop-key">Desc</span><span class="prop-val" id="propDesc">-</span></div>
            </div>
            <div class="prop-section">
              <div class="prop-section-title">布局信息</div>
              <div class="prop-row"><span class="prop-key">Bounds</span><span class="prop-val" id="propBounds">-</span></div>
              <div class="prop-row"><span class="prop-key">Size</span><span class="prop-val" id="propSize">-</span></div>
              <div class="prop-row"><span class="prop-key">Center</span><span class="prop-val" id="propCoords">-</span></div>
            </div>
            <div class="prop-section">
              <div class="prop-section-title">交互状态</div>
              <div class="prop-tags" id="propTags"></div>
            </div>
            <div class="prop-section" style="padding-bottom:10px">
              <button type="button" class="prop-tap" id="propTap" disabled>Tap Element</button>
            </div>
          </div>

          <div class="input-bar">
            <input type="text" id="textInput" placeholder="输入文本后按 Enter 发送…">
            <button type="button" data-act="send">发送</button>
          </div>
        </div>
      </div>

      <div class="ctx-menu" id="ctxMenu" hidden>
        <div class="item" data-ctx="tap">点击元素<span class="shortcut">双击</span></div>
        <div class="item" data-ctx="copy-bounds">复制范围</div>
      </div>`

    dispose = disposer()
    selected = -1
    currentXml = ''
    nodes = []
    children = new Map()
    collapsed = new Set()
    pressStart = null
    dragStart = null

    // ── 工具条 ──
    root.querySelector('[data-act="capture"]').addEventListener('click', capture)
    root.querySelector('[data-act="tree"]').addEventListener('click', refreshTree)
    root.querySelector('[data-act="collapse"]').addEventListener('click', () => {
      // 折叠选中节点而不是全部：全部折叠会把用户刚展开的那条路径也收掉，
      // 而这里通常只想起把当前这棵子树收起来
      if (selected >= 0) collapsed.add(selected)
      renderTree()
    })
    root.querySelector('[data-act="expand"]').addEventListener('click', () => {
      collapsed = new Set()
      renderTree()
    })
    root.querySelector('[data-act="send"]').addEventListener('click', () => {
      const text = $('textInput').value.trim()
      if (!text) {
        setOpState('请输入文本', 'err')
        return
      }
      // 发送后清空输入框，与旧版一致：一屏通常要连着输几次
      act('输入文本', (deviceId) => api.input(deviceId, text)).then(() => {
        $('textInput').value = ''
      })
    })

    dispose.add(
      listen($('textInput'), 'keydown', (event) => {
        if (event.key !== 'Enter') return
        event.preventDefault()
        root.querySelector('[data-act="send"]').click()
      }),
    )

    root.querySelectorAll('[data-key]').forEach((btn) => {
      dispose.add(listen(btn, 'click', () => act(`按键 ${btn.textContent}`, (id) => api.key(id, btn.dataset.key))))
    })

    // ── 自动刷新开关：选择即时生效并记忆，下一次进页面还是这个选择 ──
    const autoCaptureBox = $('autoCaptureToggle')
    const autoTreeBox = $('autoTreeToggle')
    autoCaptureBox.checked = autoCapture
    autoTreeBox.checked = autoTree
    dispose.add(
      listen(autoCaptureBox, 'change', () => {
        autoCapture = autoCaptureBox.checked
        localStorage.setItem(AUTO_CAPTURE_KEY, autoCapture ? '1' : '0')
      }),
    )
    dispose.add(
      listen(autoTreeBox, 'change', () => {
        autoTree = autoTreeBox.checked
        localStorage.setItem(AUTO_TREE_KEY, autoTree ? '1' : '0')
      }),
    )

    // ── 树 ──
    dispose.add(
      listen($('treeSearch'), 'input', () => {
        if (currentXml) renderTree()
      }),
    )

    dispose.add(
      listen($('treeContent'), 'click', (event) => {
        const toggle = event.target.closest('[data-toggle]')
        if (toggle) {
          event.stopPropagation()
          const index = Number(toggle.dataset.toggle)
          if (collapsed.has(index)) collapsed.delete(index)
          else collapsed.add(index)
          renderTree()
          return
        }
        const row = event.target.closest('.node')
        if (row) selectNode(Number(row.dataset.index))
      }),
    )

    dispose.add(
      listen($('treeContent'), 'dblclick', (event) => {
        if (event.target.closest('.node')) tapSelected()
      }),
    )

    dispose.add(
      listen($('treeContent'), 'contextmenu', (event) => {
        const row = event.target.closest('.node')
        if (!row) return
        event.preventDefault()
        selectNode(Number(row.dataset.index))
        showContextMenu(event.clientX, event.clientY, selected)
      }),
    )

    dispose.add(listen($('propTap'), 'click', tapSelected))

    dispose.add(
      listen($('breadcrumb'), 'click', (event) => {
        const span = event.target.closest('[data-jump]')
        if (span) selectNode(Number(span.dataset.jump))
      }),
    )

    // ── 画面：左键三种手势，右键取消 ──
    const frame = $('screenFrame')

    /** 抬手（或取消）时按实际按住时长分派：太短是点击，够久是长按，移动过就是滑动。 */
    const settle = (clientX, clientY) => {
      const heldMs = Math.max(0, performance.now() - pressStartedAt)
      const from = dragStart
      const at = toDevice(clientX, clientY)
      stopPress()
      dragStart = null
      clearSwipe()

      if (from && at) {
        const moved = Math.abs(at.x - from.x) > SWIPE_MIN || Math.abs(at.y - from.y) > SWIPE_MIN
        if (moved) {
          ripple(at, 'var(--purple)')
          swipeFrom(from, at, Math.round(heldMs))
          return
        }
      }
      const point = at || pressStart
      if (!point) return
      ripple(point, heldMs >= TAP_MAX_MS ? 'var(--orange)' : 'var(--green)')
      if (heldMs >= TAP_MAX_MS) longPressAt(point.x, point.y, Math.round(heldMs))
      else tapAt(point.x, point.y)
    }

    dispose.add(
      listen(frame, 'mousedown', (event) => {
        if (event.button === 2) {
          // 右键取消：手势中途改主意时，不该把已经按下的那一下发出去
          event.preventDefault()
          stopPress()
          dragStart = null
          clearSwipe()
          setOpState('已取消')
          return
        }
        if (event.button !== 0) return
        const at = toDevice(event.clientX, event.clientY)
        if (!at) return
        pressStart = at
        pressStartedAt = performance.now()
        pressIndicatorRef = pressIndicator(at)
        const tick = (now) => {
          if (!pressIndicatorRef || !pressStart) return
          pressIndicatorRef.tick(now - pressStartedAt)
          pressTicker = requestAnimationFrame(tick)
        }
        pressTicker = requestAnimationFrame(tick)
      }),
    )

    // 抬手挂在 window 上：指针移出画面后松开也要能收尾，否则手势会一直悬着
    dispose.add(
      listen(window, 'mouseup', (event) => {
        if (!pressStart && !dragStart) return
        if (event.button === 2) return
        settle(event.clientX, event.clientY)
      }),
    )

    dispose.add(
      listen(frame, 'mousemove', (event) => {
        const at = toDevice(event.clientX, event.clientY)
        const cross = $('crosshair')
        const tip = $('coordTip')
        if (!at) {
          cross.classList.remove('is-on')
          tip.hidden = true
          return
        }

        cross.classList.add('is-on')
        $('crossH').setAttribute('y1', at.cy)
        $('crossH').setAttribute('y2', at.cy)
        $('crossV').setAttribute('x1', at.cx)
        $('crossV').setAttribute('x2', at.cx)
        tip.hidden = false
        tip.textContent = `${at.x}, ${at.y}`
        // 提示条贴边时向内翻转，而不是溢出被裁：光标在右缘时往左弹、在顶缘时往下弹。
        // 先写入文字再量尺寸，offsetWidth/Height 才是这段文字的真实占位
        const tipW = tip.offsetWidth
        const tipH = tip.offsetHeight
        const overflowRight = at.cx + 16 + tipW > at.rect.width
        tip.style.left = `${overflowRight ? at.cx - 16 - tipW : at.cx + 16}px`
        const overflowTop = at.cy - tipH - 8 < 0
        tip.style.top = `${overflowTop ? at.cy + 18 : at.cy - tipH - 8}px`

        // 按住后移动超过阈值：这一下不再是点击/长按，转为滑动
        if (pressStart) {
          const dx = Math.abs(at.cx - pressStart.cx)
          const dy = Math.abs(at.cy - pressStart.cy)
          if (dx > DRAG_THRESHOLD || dy > DRAG_THRESHOLD) {
            dragStart = pressStart
            pressIndicatorRef?.clear()
            pressIndicatorRef = null
            if (pressTicker !== null) {
              cancelAnimationFrame(pressTicker)
              pressTicker = null
            }
            pressStart = null
          }
        }

        if (dragStart) drawSwipe(dragStart, at)
      }),
    )

    dispose.add(
      listen(frame, 'mouseleave', () => {
        $('crosshair').classList.remove('is-on')
        $('coordTip').hidden = true
      }),
    )

    dispose.add(listen(frame, 'contextmenu', (event) => event.preventDefault()))

    // 窗口尺寸变了，高亮框的百分比虽然还对着，但拖动中的箭头坐标要重算
    dispose.add(
      listen(window, 'resize', () => {
        highlight(selected)
        clearSwipe()
      }),
    )

    // ── 右键菜单 ──
    dispose.add(
      listen($('ctxMenu'), 'click', (event) => {
        const item = event.target.closest('[data-ctx]')
        if (!item) return
        const node = nodes[ctxIndex]
        hideContextMenu()
        if (!node) return
        if (item.dataset.ctx === 'tap') tapSelected()
        else copy(node.bounds || '', '范围')
      }),
    )
    dispose.add(listen(document, 'click', hideContextMenu))

    dispose.add(
      subscribe('view', (id) => {
        if (id !== 'inspector') dispose.dispose()
      }),
    )
  },

  unmount() {
    dispose.dispose()
    stopPress()
    dragStart = null
    nodes = []
    children = new Map()
    collapsed = new Set()
    currentXml = ''
  },
}
