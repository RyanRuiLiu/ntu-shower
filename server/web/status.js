/**
 * 状态视图：KPI + 门牌墙 + 控制操作 + 事件日志。
 *
 * 布局与视觉取自旧系统的 shower-server/app/debug/index.html。文案改用
 * docs/CONTENT.md 的术语表（例如"使用中"而非旧的"淋浴中"），因为术语统一
 * 是跨端的硬要求，而配色与排版不变。
 */

import { $, api, clearLog, getLogs, log, on, store, toast } from './app.js'

/** 七个状态的中文与配色类名。与 docs/CONTENT.md 的状态表一一对应。 */
const STATES = {
  free: { label: '空闲', cls: 'cg' },
  waiting: { label: '已入门', cls: 'cb' },
  bathing: { label: '使用中', cls: 'ca' },
  paused: { label: '已暂停', cls: 'co' },
  selected: { label: '选中', cls: 'cp' },
  repair: { label: '报修', cls: 'cr' },
  disabled: { label: '禁用', cls: 'ct' },
}

/** KPI 卡片：总喷头 + 前五个状态各自一格（与旧面板一致）。 */
const KPI = [
  { key: 'total', label: '总喷头', cls: 'ct' },
  { key: 'free', label: '空闲', cls: 'cg' },
  { key: 'selected', label: '选中', cls: 'cp' },
  { key: 'waiting', label: '已入门', cls: 'cb' },
  { key: 'bathing', label: '使用中', cls: 'ca' },
  { key: 'paused', label: '已暂停', cls: 'co' },
]

function renderKpi(doors) {
  const counts = { total: doors.length }
  for (const key of Object.keys(STATES)) counts[key] = 0
  for (const door of doors) {
    if (counts[door.state] !== undefined) counts[door.state] += 1
  }

  $('kpi').innerHTML = KPI.map(({ key, label, cls }) => {
    const value = counts[key] ?? 0
    const pct = counts.total > 0 ? Math.round((value / counts.total) * 100) : 0
    return `
      <div class="kpi-card">
        <span class="kpi-label">${label}</span>
        <span class="kpi-num ${cls}" data-kpi="${key}">${value}</span>
        <div class="kpi-bar"><div class="kpi-fill ${cls}" style="width:${pct}%"></div></div>
      </div>`
  }).join('')
}

function renderDoors(doors) {
  const box = $('doors')

  if (doors.length === 0) {
    box.innerHTML = '<div class="empty">等待设备上报喷头数据…</div>'
    return
  }

  box.innerHTML = doors
    .map((door) => {
      const state = STATES[door.state] ?? { label: door.state, cls: '' }
      // 有凭证说明有人占用；空字符串表示无人
      const occupant = door.cert_no || '—'
      return `
        <article class="door" data-door="${door.door_no}">
          <div class="door-head">
            <span class="door-no">${door.door_no}</span>
            <span class="door-tag ${door.state}">${state.label}</span>
          </div>
          <div class="door-sid">${occupant}</div>
          <div class="door-row"><span class="l">洗浴时长</span><span class="v ${door.bath_time_s > 0 ? 'live' : 'dim'}">${formatDuration(door.bath_time_s)}</span></div>
          <div class="door-row"><span class="l">上报时间</span><span class="v dim">${formatTime(door.reported_at)}</span></div>
        </article>`
    })
    .join('')
}

/** 秒数转"12 分 34 秒"；0 表示未开始。 */
function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '—'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m} 分 ${s} 秒` : `${s} 秒`
}

function formatTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('zh-CN', { hour12: false })
}

/** 让变化的门牌闪一下——提示"这里刚动了"，不是装饰。 */
function flashDoor(doorNo) {
  const el = document.querySelector(`.door[data-door="${doorNo}"]`)
  if (!el) return
  el.classList.remove('flash')
  // 强制回流，让同名动画能重新触发
  void el.offsetWidth
  el.classList.add('flash')
}

// ── 操作 ──────────────────────────────────────────────────
function setResult(id, ok, text) {
  const el = $(id)
  el.className = `res ${ok ? 'ok' : 'err'}`
  el.textContent = text
}

/** 操作所需的三个输入。分配用得上全部，结算不用喷头号。 */
function readInputs(needDoor) {
  const doorNo = Number($('inDoor').value)
  const certNo = $('inSid').value.trim()
  const password = $('inPwd').value

  if (!certNo) throw new Error('请填写学号')
  if (!password) throw new Error('请填写密码')
  if (needDoor && (!doorNo || doorNo < 1)) throw new Error('请填写喷头编号')

  return { doorNo, certNo, password }
}

function requireDevice() {
  if (!store.deviceId) throw new Error('尚未获取到设备，请等待设备上线')
  return store.deviceId
}

async function doAssign() {
  const btn = $('btnAssign')
  btn.disabled = true
  try {
    const { doorNo, certNo, password } = readInputs(true)
    const result = await api.assign({
      device_id: requireDevice(),
      door_no: doorNo,
      cert_no: certNo,
      password,
    })
    setResult('resAction', true, `已分配 ${doorNo} 号喷头`)
    log('o', `分配成功：${doorNo} 号 ← ${certNo}（${result.status}）`)
  } catch (err) {
    setResult('resAction', false, err.message)
    log('x', `分配失败：${err.message}`)
  } finally {
    btn.disabled = false
  }
}

async function doSettle() {
  const btn = $('btnSettle')
  btn.disabled = true
  try {
    const { certNo, password } = readInputs(false)
    const result = await api.settle({
      device_id: requireDevice(),
      cert_no: certNo,
      password,
    })
    setResult('resAction', true, `已结算 ${certNo} 的会话`)
    log('o', `结算成功：${certNo}（${result.status}）`)
  } catch (err) {
    setResult('resAction', false, err.message)
    log('x', `结算失败：${err.message}`)
  } finally {
    btn.disabled = false
  }
}

async function doPhoto() {
  const btn = $('btnPhoto')
  btn.disabled = true
  try {
    const result = await api.photo({ device_id: requireDevice() })
    setResult('resPhoto', true, '已拍摄')
    // 结果里带 base64 图；服务端对失败会走 ApiError，不会到这里
    if (result?.data?.image_base64) {
      $('photoImg').src = `data:image/jpeg;base64,${result.data.image_base64}`
      $('photoBox').style.display = 'block'
    }
    log('o', '拍照成功')
  } catch (err) {
    setResult('resPhoto', false, err.message)
    log('x', `拍照失败：${err.message}`)
  } finally {
    btn.disabled = false
  }
}

// ── 日志 ──────────────────────────────────────────────────
const LOG_LABEL = { i: '信息', s: '状态', o: '成功', x: '失败', r: '警告' }

function renderLog(entries) {
  $('logCount').textContent = String(entries.length)
  $('log').innerHTML = entries
    .map(
      (e) => `
      <div class="log-row">
        <span class="log-t">${e.at.toLocaleTimeString('zh-CN', { hour12: false })}</span>
        <span class="log-e ${e.level}">${LOG_LABEL[e.level] ?? e.level}</span>
        <span class="log-m">${escapeHtml(e.message)}</span>
      </div>`,
    )
    .join('')
}

function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

// ── 挂载 ──────────────────────────────────────────────────
export function mountStatus() {
  // 折叠区段：点击标题切换
  for (const head of document.querySelectorAll('[data-toggle]')) {
    head.addEventListener('click', () => {
      $(head.dataset.toggle).classList.toggle('collapsed')
    })
  }

  // 日志展开：点某一行展开其完整内容
  $('log').addEventListener('click', (e) => {
    e.target.closest('.log-m')?.classList.toggle('expand')
  })

  $('btnAssign').addEventListener('click', doAssign)
  $('btnSettle').addEventListener('click', doSettle)
  $('btnPhoto').addEventListener('click', doPhoto)
  $('btnClearLog').addEventListener('click', (e) => {
    e.stopPropagation()
    clearLog()
    toast('日志已清空')
  })

  on('doors', (doors) => {
    renderKpi(doors)
    renderDoors(doors)
  })
  on('flash', flashDoor)
  on('log', renderLog)

  renderKpi(store.doors)
  renderDoors(store.doors)
  renderLog(getLogs())
}
