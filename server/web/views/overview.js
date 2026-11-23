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
import { deviceCell } from '../core/cells.js'
import { elapsedSince, fmtDateTime, fmtDuration, fmtFullDateTime, fmtTime, serverNowMs } from '../core/clock.js'
import { NEED_DEVICE } from '../core/copy.js'
import { DOOR_STATES } from '../core/contract.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { outcomeText, problemText, report } from '../core/outcome.js'
import { store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'
import {
  canAuthorize,
  canCut,
  refreshWater,
  subscribeWater,
  waterActionResult,
  WATER_COPY,
  WATER_STATE,
  waterDoor,
  waterStatusText,
  waterStatusTitle,
} from '../core/water.js'

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

/** 正在发送的授权出水/断水：`doorNo:action`，用于按钮禁用，避免同一喷头连点。 */
let waterPending = new Set()

function isAdmin() {
  return store.runtime.length > 0
}

function waterKey(doorNo, action) {
  return `${doorNo}:${action}`
}

function doorWaterDetail(door) {
  const found = waterDoor(door.door_no)
  // 管理员即使还没读到水控数据，也占住这两行：刷新完成前后卡片高度不跳。
  if (!found && !isAdmin()) return ''
  const flow = found ? fmtDuration(found.door.allopen_s) : PLACEHOLDER
  const auth = found ? fmtDuration(found.door.bath_s) : PLACEHOLDER
  // 水控板自己的计时：授权计时=授权位时间，淋浴时长=出水位时间；与会话的
  // 淋浴时长/分配时长不是同一口径，标签必须带“水控板”。
  return `
      <div class="door-grid">
        <div class="kv door-cell"><span class="k" title="从授权位出现开始计时；手动停水后继续；授权清除后归零">水控板授权计时</span><span class="v">${esc(auth)}</span></div>
        <div class="kv door-cell"><span class="k" title="出水位存在时增长；手动停水后冻结，再次出水接着累计；授权清除后归零">水控板淋浴时长</span><span class="v">${esc(flow)}</span></div>
      </div>`
}

function doorWaterControls(door) {
  const found = waterDoor(door.door_no)
  const admin = isAdmin()
  if (!found && !admin) return ''
  const statusText = found ? waterStatusText(found.door.status) : WATER_STATE.unknown.label
  const statusTitle = found ? waterStatusTitle(found.door.status) : WATER_STATE.unknown.title
  const status = `<span class="badge" title="${esc(statusTitle)}">水控板 ${esc(statusText)}</span>`
  const doorNo = door.door_no
  const opening = waterPending.has(waterKey(doorNo, 'open'))
  const closing = waterPending.has(waterKey(doorNo, 'close'))
  const authorizeEnabled = canAuthorize(found ? found.door.status : null)
  const cutEnabled = canCut(found ? found.door.status : null)
  const buttons = admin
    ? `<div class="btn-row">
        <button type="button" class="btn btn-sm" data-water="open" data-door="${esc(doorNo)}" title="${esc(WATER_COPY.authorizeTitle)}" ${opening || !authorizeEnabled ? 'disabled' : ''}>${esc(WATER_COPY.authorize)}</button>
        <button type="button" class="btn btn-ghost btn-sm" data-water="close" data-door="${esc(doorNo)}" title="${esc(WATER_COPY.cutTitle)}" ${closing || !cutEnabled ? 'disabled' : ''}>${esc(WATER_COPY.cut)}</button>
      </div>`
    : ''
  return `<div class="door-controls">${status}${buttons}</div>`
}

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
        <div class="kpi-bar"><div class="kpi-fill" data-ratio="${(ratio * 100).toFixed(1)}"></div></div>
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
          ${doorWaterControls(door)}
          ${doorWaterDetail(door)}
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
  tickTimer = setInterval(() => {
    refreshDurations()
    // 喷头时长与任务倒计时共用同一根秒针
    refreshTaskCountdowns()
  }, 1000)
}

function stopTicking() {
  if (tickTimer === null) return
  clearInterval(tickTimer)
  tickTimer = null
}

function paint() {
  $('kpi').innerHTML = renderKpi(store.doors)
  // 占比是运行时几何值：只写比例变量，宽度声明留在 CSS
  for (const fill of $('kpi').querySelectorAll('.kpi-fill[data-ratio]')) {
    fill.style.setProperty('--ratio', `${fill.dataset.ratio}%`)
  }
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
    report('resPhoto', `失败 · ${NEED_DEVICE}`, 'err')
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

/** 倒计时文案：到点之前是"多久之后"，过了就是"到点执行中"。按服务端钟算。 */
function countdownText(at) {
  const remaining = Math.round((Date.parse(at) - serverNowMs()) / 1000)
  if (remaining <= 0) return '到点执行中'
  return `${fmtDuration(remaining)}后`
}

/** 每秒把看板上的倒计时重算一遍（与喷头时长的秒针共用同一条定时器）。 */
function refreshTaskCountdowns() {
  for (const node of $('taskBoard').querySelectorAll('[data-at]')) {
    node.textContent = countdownText(node.dataset.at)
  }
}

/**
 * 四个后台任务各一段：开关与名单、下次执行（倒计时）、每条在等动作的完整明细。
 *
 * 只报告状态、不做判断：判据在服务端只有一处出处，这里把它显示出来。排定的动作按
 * 功能归到各自的任务段；power 不走调度器，它自己的现状是"hold 状态 + MCU
 * 租约到点 + 恢复条件逐项"。设备名用全站同一种单元格（悬停看 id），任务板只额外
 * 加粗。倒计时是这块的锚点——每秒走字，其余一切安静。
 */
function renderTaskBoard(data) {
  const scheduledByTask = {}
  for (const row of data.scheduled) (scheduledByTask[row.job] ||= []).push(row)

  const taskSections = data.jobs.map((task) => {
    const rows = scheduledByTask[task.key] || []
    const nextAt = rows.length ? rows.map((row) => row.at).sort()[0] : ''
    const head = `
      <div class="task-head">
        <span class="task-name">${esc(task.title)}</span>
        <span class="task-chip ${task.enabled ? 'on' : 'off'}">${task.enabled ? '启用' : '停用'}</span>
        <span class="task-meta">名单 ${task.students_count} 人</span>
        ${nextAt ? `<span class="task-next">下次执行 <span data-at="${esc(nextAt)}">${esc(countdownText(nextAt))}</span></span>` : ''}
      </div>`

    if (task.key === 'power') {
      const body =
        data.holds.length === 0
          ? '<div class="task-hint">当前没有设备在延长中</div>'
          : data.holds
              .map((hold) => {
                // 状态判据只有服务端 hold.state 一处：pending/arming 都还没施加，
                // 既没有拨钟，也没有恢复时刻，不能显示“恢复时刻未到”。
                const applied = hold.state === 'applied'
                const pendingText = hold.state === 'arming' ? '布防已写入，待拨钟' : '尚未施加'
                const flags = applied
                  ? [
                      { ok: hold.past_close, yes: '恢复时刻已过', no: '恢复时刻未到' },
                      { ok: hold.idle, yes: '喷头空闲', no: '仍有会话' },
                    ]
                      .map(({ ok, yes, no }) => `<span class="task-flag ${ok ? 'ok' : 'no'}">${ok ? yes : no}</span>`)
                      .join('')
                  + (hold.past_close && hold.idle ? '<span class="task-flag ok">即将恢复</span>' : '')
                  : `<span class="task-flag no">${pendingText}</span>`
                const pending =
                  hold.pending.length === 0
                    ? ''
                    : `<div class="task-sub">待重分配 ${hold.pending.length} 人：${hold.pending
                        .map((item) => `#${esc(item.door_no)} ${esc(item.cert_no)}`)
                        .join('  ')}</div>`
                return `
                <div class="task-entry">
                  <div class="task-line">
                    ${deviceCell(hold.device_id, 'task-dev')}
                    <span class="muted">${applied ? 'hold 中' : '待生效'}（${esc(hold.hold_source === 'manual' ? '手动' : '自动')}，${esc(fmtDateTime(hold.hold_started_at))}）</span>
                  </div>
                  <div class="task-sub">
                    ${applied ? `<span>恢复时刻 <span class="mono">${esc(fmtFullDateTime(hold.resume_at))}</span></span>` : ''}
                    <span>MCU 布防截止 <span class="mono">${esc(fmtFullDateTime(hold.arming_deadline))}</span></span>
                    <span>已重分配 ${hold.reassigned_count}/${hold.total}</span>
                    ${flags}
                  </div>
                  ${pending}
                </div>`
              })
              .join('')
      return `<div class="task-section">${head}${body}</div>`
    }

    const body =
      rows.length === 0
        ? '<div class="task-hint">当前没有排定的动作</div>'
        : rows
            .map((row) => {
              const chain =
                row.max_duration_s === null
                  ? ''
                  : `<span>本轮自 ${esc(fmtDateTime(row.run_started_at))}</span>` +
                    `<span>上限 ${fmtDuration(row.max_duration_s)}</span>`
              return `
              <div class="task-entry">
                <div class="task-line">
                  ${deviceCell(row.device_id, 'task-dev')}
                  <span>#${esc(row.door_no)}</span>
                  <span class="mono">${esc(row.cert_no || PLACEHOLDER)}</span>
                  <span class="task-at">下次动手 ${esc(fmtDateTime(row.at))}</span>
                </div>
                <div class="task-sub">
                  <span>会话 #${esc(row.session_id)}</span>
                  <span>${esc(row.state)}</span>
                  <span>起算 ${esc(fmtDateTime(row.started_at))}</span>
                  <span>阈值 ${fmtDuration(row.threshold_s)}</span>
                  ${chain}
                </div>
              </div>`
            })
            .join('')
    return `<div class="task-section">${head}${body}</div>`
  })

  return taskSections.join('')
}

async function refreshTaskBoard() {
  try {
    const data = await api.jobsStatus()
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

/** 卡片上的授权出水/断水：admin 才能看到按钮，服务端仍会再校验。 */
async function onWaterButton(event) {
  const button = event.target.closest('[data-water]')
  if (!button || button.disabled) return
  const doorNo = Number(button.dataset.door)
  const action = button.dataset.water
  if (!store.deviceId || !doorNo || (action !== 'open' && action !== 'close')) return
  const key = waterKey(doorNo, action)
  if (waterPending.has(key)) return
  waterPending.add(key)
  paint()
  try {
    const result =
      action === 'open'
        ? await api.waterOpen(store.deviceId, doorNo)
        : await api.waterClose(store.deviceId, doorNo)
    if (result.status !== 'ok') {
      toast(outcomeText(result))
      return
    }
    // 先刷新再提示：结果文案按板上实际状态选，避免“已出水”与“待按电磁阀”说反。
    await refreshWater(store.deviceId)
    const found = waterDoor(doorNo)
    toast(waterActionResult(action, found ? found.door.status : null))
  } catch (error) {
    toast(problemText(error))
  } finally {
    waterPending.delete(key)
    paint()
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
    // 30 秒轮询覆盖"状态不变就没有帧"的空窗
    dispose.add(subscribe('doors', () => { paint(); refreshTaskBoard() }))
    dispose.add(subscribe('view', (id) => { if (id !== 'overview') dispose.dispose() }))

    dispose.add(listen($('btnPhoto'), 'click', doPhoto))
    dispose.add(listen($('btnAssign'), 'click', doAssign))
    dispose.add(listen($('btnSettle'), 'click', doSettle))
    // 卡片上的水控按钮只操作当前设备；刷新入口在顶栏设备信息左侧。
    dispose.add(listen($('doors'), 'click', onWaterButton))
    dispose.add(subscribe('runtime', () => paint()))
    dispose.add(subscribe('device', () => {
      waterPending = new Set()
      paint()
    }))
    dispose.add(subscribeWater(() => paint()))

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
