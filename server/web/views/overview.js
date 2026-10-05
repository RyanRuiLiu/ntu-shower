/**
 * 概览：喷头现状 + 会话操作。
 *
 * KPI 六格 → 喷头卡片墙 → 控制操作两卡，样式在 style/overview.css。喷头卡片按
 * 状态决定显示哪些明细行：占用中的喷头给出学号、分配/开阀/关阀时间与各项时长，
 * 时长按当前时刻实时算——只显示一个状态词判断不出喷头是否卡住。
 *
 * 数据取 GET /doors，管理面写操作同域。
 */
import { api } from '../core/api.js'
import { elapsedSince, fmtDateTime, fmtDuration, fmtTime } from '../core/clock.js'
import { DOOR_STATES } from '../core/contract.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { outcomeText, problemText, report } from '../core/outcome.js'
import { store, subscribe } from '../core/store.js'

/**
 * 七个状态的中文名。键取自契约（core/contract.js），文案与小程序一致：同一批
 * 用户在两处看同一件事，各写一套必然漂移。服务端加了状态而这里缺文案时启动
 * 即失败，不在卡片上静默显示英文。
 */
const LABELS = {
  free: '空闲',
  selected: '已选中',
  waiting: '等待开阀',
  paused: '已暂停',
  bathing: '淋浴中',
  disabled: '已禁用',
  repair: '维修中',
}

for (const state of DOOR_STATES) {
  if (!LABELS[state]) throw new Error(`状态 ${state} 缺中文文案（见 views/overview.js 的 LABELS）`)
}

/** 占用中的状态：只有这些喷头卡片才展开明细。 */
const OCCUPIED = new Set(['waiting', 'bathing', 'paused'])

/** KPI 六格。状态文案与小程序一致：同一批用户在两处看同一件事。 */
const KPI = [
  { key: 'total', label: '总喷头', tone: 'tone-gray' },
  { key: 'free', label: '空闲', tone: 'tone-green' },
  { key: 'selected', label: '已选中', tone: 'tone-purple' },
  { key: 'waiting', label: '等待开阀', tone: 'tone-blue' },
  { key: 'bathing', label: '淋浴中', tone: 'tone-amber' },
  { key: 'paused', label: '已暂停', tone: 'tone-orange' },
]

/** 上一次渲染时各喷头的状态，用于判断"这个喷头刚变了"。 */
let previousStates = new Map()

function renderKpi(doors) {
  const counts = { total: doors.length }
  for (const key of Object.keys(LABELS)) counts[key] = 0
  for (const door of doors) {
    if (counts[door.state] !== undefined) counts[door.state] += 1
  }
  const total = doors.length || 1
  return KPI.map(({ key, label, tone }) => {
    const value = counts[key] ?? 0
    // 总数那条恒满格：它是分母，画成占比没有意义
    const ratio = key === 'total' ? 1 : value / total
    return `
      <div class="kpi-card ${tone}">
        <span class="kpi-label">${esc(label)}</span>
        <span class="kpi-num">${value}</span>
        <div class="kpi-bar"><div class="kpi-fill" style="width:${(ratio * 100).toFixed(1)}%"></div></div>
      </div>`
  }).join('')
}

/**
 * 喷头卡片上显示哪几格，由状态决定。
 *
 * 只列进行中的会话那几项——喷头空闲时这个网格整个不渲染，喷头卡片只剩头部一行。
 * 上次使用不在这里，它固定在头部右侧（见 [renderDoors]）。
 *
 * 协议里 `open_time` / `in_time` / `close_time` 一直都有（未发生的为空），所以按需
 * 取即可：摆六格空位只是噪声。
 */
const DOOR_FIELDS = {
  waiting: ['open_time', 'assigned'],
  bathing: ['open_time', 'assigned', 'in_time', 'bath'],
  paused: ['open_time', 'assigned', 'in_time', 'bath', 'close_time', 'pause'],
}

const DOOR_LABELS = {
  open_time: '分配时间',
  assigned: '分配时长',
  in_time: '开阀时间',
  bath: '淋浴时长',
  close_time: '关阀时间',
  pause: '暂停时长',
}

/**
 * 时间与时长同属有效信息，用正文色；只有正在走的时长带强调色。要区分的是
 * "哪个数字在动"，不是"哪个更重要"。
 */
const FIELD_TONE = {
  open_time: 'time',
  in_time: 'time',
  close_time: 'time',
  assigned: 'live',
  bath: 'running',
  pause: 'warn',
}

/** 需要每秒重算的字段。 */
const DURATION_FIELDS = new Set(['assigned', 'bath', 'pause'])

/** 一个时长字段在当前时刻的值；该状态下没有这一项时返回空串。 */
function durationOf(door, field) {
  if (field === 'assigned') {
    return OCCUPIED.has(door.state) ? fmtDuration(elapsedSince(door.open_time)) : ''
  }
  if (field === 'bath') {
    if (door.state !== 'bathing' && door.state !== 'paused') return ''
    // 暂停期间出水时钟停走，只算已累计的秒数
    const seconds = door.state === 'paused' ? door.bath_time_s : door.bath_time_s + elapsedSince(door.in_time)
    return fmtDuration(seconds)
  }
  if (field === 'pause') {
    return door.state === 'paused' ? fmtDuration(elapsedSince(door.close_time)) : ''
  }
  return ''
}

/**
 * 头部右侧那一格：上次真正使用（开过阀）的时刻。没使用过显示占位符。
 */
function lastUsedText(door) {
  return fmtDateTime(door.last_used_at)
}

function fieldValue(door, field) {
  if (DURATION_FIELDS.has(field)) return durationOf(door, field)
  return field.includes('_time') ? fmtTime(door[field]) : ''
}

function doorCell(door, field) {
  return (
    `<div class="kv door-cell"><span class="k">${esc(DOOR_LABELS[field])}</span>` +
    `<span class="v ${FIELD_TONE[field]}" data-field="${esc(field)}">${esc(fieldValue(door, field))}</span></div>`
  )
}

function renderDoors(doors) {
  if (doors.length === 0) {
    previousStates = new Map()
    return '<div class="empty">等待设备上报喷头数据…</div>'
  }

  const list = doors.slice().sort((a, b) => a.door_no - b.door_no)
  const current = new Map()
  const html = list
    .map((door) => {
      current.set(door.door_no, door.state)
      const changed = previousStates.has(door.door_no) && previousStates.get(door.door_no) !== door.state
      const fields = DOOR_FIELDS[door.state] || []
      // 学号只在占用中显示：喷头空闲时设备已把它清零，那一行只会是占位符
      const certLine = OCCUPIED.has(door.state) ? `<div class="door-cert mono">${esc(door.cert_no || PLACEHOLDER)}</div>` : ''
      const grid = fields.length
        ? `<div class="door-grid">${fields.map((f) => doorCell(door, f)).join('')}</div>`
        : ''

      return `
        <div class="door${changed ? ' flash' : ''}" data-door="${esc(door.door_no)}">
          <div class="door-head">
            <span class="door-no">#${esc(door.door_no)}</span>
            <span class="door-tag ${esc(door.state)}">${esc(LABELS[door.state] || door.state)}</span>
            <span class="kv door-cell door-last-used">
              <span class="k">上次使用时间</span>
              <span class="v time">${esc(lastUsedText(door))}</span>
            </span>
          </div>
          ${certLine}
          ${grid}
        </div>`
    })
    .join('')

  previousStates = current
  return html
}

/**
 * 每秒把在走的时长重算一遍。
 *
 * 不能只靠推送刷新：时长是"现在减开始时刻"，而推送间隔由设备决定（几秒到十几秒一
 * 次）。只等它的话数字是一跳一跳地走，看着就不像在计时。
 */
function refreshDurations() {
  for (const node of $('doors').querySelectorAll('[data-field]')) {
    const field = node.dataset.field
    if (!DURATION_FIELDS.has(field)) continue
    const doorNo = Number(node.closest('.door')?.dataset.door)
    const door = store.doors.find((item) => item.door_no === doorNo)
    if (!door) continue
    const text = durationOf(door, field)
    if (text && node.textContent !== text) node.textContent = text
  }
}

let tickTimer = null

function startTicking() {
  if (tickTimer !== null) return
  tickTimer = setInterval(refreshDurations, 1000)
}

function stopTicking() {
  if (tickTimer === null) return
  clearInterval(tickTimer)
  tickTimer = null
}

function paint() {
  $('kpi').innerHTML = renderKpi(store.doors)
  $('doors').innerHTML = renderDoors(store.doors)
  // 闪一下就把标记撤掉，否则下次重排还会带着它
  for (const card of $('doors').querySelectorAll('.door.flash')) {
    setTimeout(() => card.classList.remove('flash'), 1000)
  }
}

// ── 会话操作 ───────────────────────────────────────────────

function credentials() {
  const certNo = $('inSid').value.trim()
  const password = $('inPwd').value
  if (!certNo || !password) {
    report('resAction', '失败 · 请填写学号与密码', 'err')
    return null
  }
  return { cert_no: certNo, password }
}

async function doAssign() {
  const doorNo = Number($('inDoor').value)
  const base = credentials()
  if (!base) return
  if (!doorNo) {
    report('resAction', '失败 · 请先填写喷头编号', 'err')
    return
  }
  report('resAction', '正在分配…', 'info')
  try {
    const result = await api.assign(store.deviceId, { door_no: doorNo, ...base })
    report('resAction', outcomeText(result), result.status === 'ok' ? 'ok' : 'err')
  } catch (error) {
    report('resAction', problemText(error), 'err')
  }
}

async function doSettle() {
  const base = credentials()
  if (!base) return
  report('resAction', '正在结算…', 'info')
  try {
    const result = await api.settle(store.deviceId, base)
    report('resAction', outcomeText(result), result.status === 'ok' ? 'ok' : 'err')
  } catch (error) {
    report('resAction', problemText(error), 'err')
  }
}

async function doPhoto() {
  if (!store.deviceId) {
    report('resPhoto', '失败 · 请先在顶栏选择设备', 'err')
    return
  }
  report('resPhoto', '正在拍照…', 'info')
  try {
    const result = await api.photo(store.deviceId)
    // 先看命令成没成，再看画面在不在：只看 image 字段会把"拍到了但没图"显示成成功
    if (result.status !== 'ok') {
      report('resPhoto', outcomeText(result), 'err')
      return
    }
    const image = result.data?.image_base64
    if (!image) {
      report('resPhoto', '失败 · 设备未返回画面', 'err')
      return
    }
    $('photoImg').src = `data:image/jpeg;base64,${image}`
    $('photoImg').hidden = false
    $('photoHint').hidden = true
    report('resPhoto', '已完成 · 已获取画面', 'ok')
  } catch (error) {
    report('resPhoto', problemText(error), 'err')
  }
}

// ── 后台任务看板 ──────────────────────────────────────────────────────────

/**
 * 四个后台任务各一段：开关与名单、此刻在等什么。
 *
 * 只报告状态、不做判断：判据在服务端只有一处出处，这里把它显示出来。排定的动作按
 * 功能归到各自的任务段下（extend_hours 不走调度器，它的现状是"延长中的设备"）；
 * 设备名沿用审计页的做法——从共享的 store.devices 映射，悬停看 id。数据随帧刷新，
 * 30 秒轮询兜住"状态不变就没有帧"的空窗。
 */
function renderTaskBoard(data) {
  const names = new Map(store.devices.map((device) => [device.device_id, device.name]))
  const deviceCell = (id) => `<span class="mono" title="${esc(id)}">${esc(names.get(id) || id)}</span>`

  const scheduledByFeature = {}
  for (const row of data.scheduled) (scheduledByFeature[row.feature] ||= []).push(row)

  return data.tasks
    .map((task) => {
      let body
      if (task.key === 'extend_hours') {
        body =
          data.extending.length === 0
            ? '<div class="muted task-hint">当前没有设备在延长中</div>'
            : data.extending
                .map((shift) => {
                  const people = shift.assignments
                    .map((item) => `#${item.door_no} ${item.cert_no}`)
                    .join('、')
                  return `
                  <div class="task-block">
                    <div class="task-row">${deviceCell(shift.device_id)}
                      <span class="muted">钟已拨快 ${esc(shift.shift_minutes)} 分钟 · 于 ${esc(fmtDateTime(shift.shifted_at))}</span>
                    </div>
                    <div class="task-row"><span class="muted">待分配回原喷头：${esc(people || '无')}</span></div>
                  </div>`
                })
                .join('')
      } else {
        const rows = scheduledByFeature[task.key] || []
        body =
          rows.length === 0
            ? '<div class="muted task-hint">当前没有排定的动作</div>'
            : rows
                .map((row) => {
                  const detail =
                    `会话 #${row.session_id} · 现态 ${esc(row.state)} · ` +
                    `起算 ${esc(fmtDateTime(row.started_at))} · 阈值 ${fmtDuration(row.threshold_s)}`
                  return `
                  <div class="task-block">
                    <div class="task-row">
                      ${deviceCell(row.device_id)}
                      <span>#${esc(row.door_no)}</span>
                      <span class="mono">${esc(row.cert_no || PLACEHOLDER)}</span>
                      <span class="time">到点 ${esc(fmtDateTime(row.at))}</span>
                    </div>
                    <div class="task-row muted">${detail}</div>
                  </div>`
                })
                .join('')
      }
      return `
      <div class="task-section-title">
        ${esc(task.title)}
        <span class="task-state ${task.enabled ? 'on' : 'off'}">${task.enabled ? '启用' : '停用'}</span>
        <span class="muted">名单 ${task.roster} 人</span>
      </div>${body}`
    })
    .join('')
}

async function refreshTaskBoard() {
  try {
    const data = await api.backgroundTasksStatus()
    $('taskBoard').innerHTML = renderTaskBoard(data)
  } catch (error) {
    $('taskBoard').innerHTML = `<div class="empty">${esc(problemText(error))}</div>`
  }
}

let taskBoardTimer = null

function startTaskBoard() {
  if (taskBoardTimer === null) {
    refreshTaskBoard()
    taskBoardTimer = setInterval(refreshTaskBoard, 30_000)
  }
}

function stopTaskBoard() {
  if (taskBoardTimer !== null) {
    clearInterval(taskBoardTimer)
    taskBoardTimer = null
  }
}

let dispose = disposer()

export const overviewView = {
  id: 'overview',
  title: '概览',

  mount(root) {
    root.innerHTML = `
      <div class="kpi" id="kpi"></div>

      <div class="doors" id="doors"><div class="empty">等待设备上报喷头数据…</div></div>

      <div class="actions">
        <div class="act-card">
          <div class="act-title">拍照</div>
          <button type="button" class="btn" id="btnPhoto">拍照</button>
          <div class="result" id="resPhoto"></div>
          <div class="photo-box">
            <span class="muted" id="photoHint">点击「拍照」获取现场画面</span>
            <img id="photoImg" alt="设备摄像头画面" hidden>
          </div>
        </div>

        <div class="act-card">
          <div class="act-title">分配 / 结算</div>
          <div class="fld">
            <label for="inDoor">喷头编号（仅分配时需要）</label>
            <input id="inDoor" type="number" min="1">
          </div>
          <div class="fld">
            <label for="inSid">学号</label>
            <input id="inSid" type="text">
          </div>
          <div class="fld">
            <label for="inPwd">密码</label>
            <input id="inPwd" type="password" placeholder="浴室密码">
          </div>
          <div class="btn-row">
            <button type="button" class="btn" id="btnAssign">分配喷头</button>
            <button type="button" class="btn" id="btnSettle">结算</button>
          </div>
          <div class="result" id="resAction"></div>
        </div>
      </div>

      <div class="act-card task-board">
        <div class="act-title">后台任务</div>
        <div id="taskBoard"><div class="empty">正在加载…</div></div>
      </div>`

    dispose = disposer()
    // 帧到即重绘喷头并刷新看板：调度器的计划随帧变，跟随帧的节奏就是实时；
    // 30 秒轮询兜住"状态不变就没有帧"的空窗
    dispose.add(subscribe('doors', () => { paint(); refreshTaskBoard() }))
    dispose.add(subscribe('view', (id) => { if (id !== 'overview') dispose.dispose() }))

    dispose.add(listen($('btnPhoto'), 'click', doPhoto))
    dispose.add(listen($('btnAssign'), 'click', doAssign))
    dispose.add(listen($('btnSettle'), 'click', doSettle))

    previousStates = new Map()
    paint()
    startTicking()
    startTaskBoard()
  },

  unmount() {
    stopTicking()
    stopTaskBoard()
    dispose.dispose()
  },
}
