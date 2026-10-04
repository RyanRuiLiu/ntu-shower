/**
 * 审计：接口调用（api_call）、命令流水（command_audit）、后台任务（background_task_run）。
 *
 * 三种审计结构不同，页面骨架却是同一个：一张关键列表 + 右侧全部字段。所以**差异只写在
 * [TABS] 里**（标题、取数、列头、每格怎么显示、时间取哪一列），渲染只有一份——加第四种
 * 审计就是加一条描述。早先每种审计各写一份表格渲染，两份几乎一样的代码各自演化；
 * 那不是风格问题，是"唯一实现"的问题。
 *
 * 表格只放关键列；点行选中，右侧详情面板常驻显示该行全部字段——详情在右栏常驻，
 * 比对几行时眼睛不用上下跑。详情面板遍历行对象的全部键渲染，后台加列自动出现，
 * 点值即复制，另有整行 JSON 复制。
 *
 * 动作名、原因码、功能名、结果名一律**照原样显示**：这是开发者页面，读它的人要拿这些值
 * 去日志里搜（`background-task.settled`、`feature=renew_wait`），翻译成中文反而搜不到。
 */
import { api } from '../core/api.js'
import { fmtFullDateTime } from '../core/clock.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { problemText, report, statusBadgeClass, statusLabel } from '../core/outcome.js'
import { store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

function statusBadge(status) {
  if (status === null || status === undefined) return PLACEHOLDER
  return `<span class="badge ${statusBadgeClass(status)}">${esc(statusLabel(status))}</span>`
}

function copy(text, what) {
  navigator.clipboard.writeText(text).then(
    () => toast(`已复制${what}`),
    () => report('auditResult', '复制失败（浏览器未授予剪贴板权限）', 'err'),
  )
}

/** 详情卡：标签 + 全量折行的值，点卡即复制。空值不渲染。 */
function detailCard(key, value) {
  if (value === null || value === undefined || value === '') return ''
  const text = String(value)
  return `<div class="detail-card" data-copy-value="${esc(text)}" title="点击复制">
      <div class="k">${esc(key)}</div>
      <div class="v mono">${esc(text)}</div>
    </div>`
}

function renderDetail(row, title) {
  const panel = $('detailPanel')
  if (!row) {
    panel.innerHTML = '<div class="empty">点击左侧任一行查看全部字段，点击字段值可复制</div>'
    return
  }
  const cards = Object.entries(row)
    .map(([key, value]) => detailCard(key, value))
    .join('')
  panel.innerHTML = `
    <div class="panel-head">
      <span class="panel-title">${esc(title)}</span>
      <div class="panel-tools">
        <button type="button" class="btn btn-ghost btn-sm" id="btnCopyRow">复制整行 JSON</button>
      </div>
    </div>
    <div class="panel-body detail-grid">${cards}</div>`
}

const mono = (text) => `<span class="mono">${esc(text ?? '')}</span>`
const muted = (text) => `<span class="muted">${esc(text ?? PLACEHOLDER)}</span>`

/** 设备列：三张表都一样，所以只写这一份。 */
function deviceCell(deviceId, names) {
  const name = deviceId ? names.get(deviceId) || deviceId : PLACEHOLDER
  return `<span title="${esc(deviceId || '')}">${esc(name)}</span>`
}

/**
 * 三种审计的差异，**只在这里**。内容以原值为主（见模块注释），所以这里不做中文映射。
 *
 * `cells` 返回每格的内部 HTML（`<td>` 由渲染那处统一包），格数与 `headers` 一一对应。
 */
const TABS = {
  api: {
    label: '接口调用',
    title: '每一次后台请求：谁、从哪个 IP、调了什么、结果如何',
    kind: 'calls',
    time: (row) => row.requested_at,
    headers: ['类别', '调用方', '设备', '请求', '状态', '耗时', '问题'],
    cells: (row, names) => [
      row.kind === 'gateway'
        ? `<span class="badge badge-blue">网关${row.status_code === 0 ? '连接' : '断开'}</span>`
        : esc(row.method),
      `${mono(row.actor_kind)}${
        row.client_ip ? `<div class="muted" title="来源 IP">${esc(row.client_ip)}</div>` : ''
      }`,
      deviceCell(row.device_id, names),
      `${mono(`${row.method} ${row.path}`)}${
        row.query ? `<span class="muted">?${esc(row.query)}</span>` : ''
      }`,
      mono(row.status_code),
      mono(`${row.duration_ms} ms`),
      muted(row.problem_type),
    ],
  },
  command: {
    label: '命令流水',
    title: '每条发到设备的命令：参数、结果、设备回报',
    kind: 'commands',
    time: (row) => row.created_at,
    headers: ['设备', '动作', '状态', '原因', '耗时'],
    cells: (row, names) => [
      deviceCell(row.device_id, names),
      mono(row.action),
      statusBadge(row.status),
      muted(row.reason_code),
      mono(row.finished_at ? `${new Date(row.finished_at) - new Date(row.created_at)} ms` : PLACEHOLDER),
    ],
  },
  task: {
    label: '后台任务',
    title: '配置好的后台任务自动替谁做了什么、为什么（时间与命令流水对照即知发了哪条命令）',
    kind: 'background-task-runs',
    time: (row) => row.occurred_at,
    headers: ['设备', '喷头', '学号', '功能', '动作', '结果', '阈值', '原因'],
    cells: (row, names) => [
      deviceCell(row.device_id, names),
      mono(row.door_no),
      mono(row.cert_no),
      mono(row.feature),
      mono(row.action),
      mono(row.outcome),
      row.threshold_s === null ? PLACEHOLDER : mono(`${row.threshold_s} 秒`),
      muted(row.reason),
    ],
  },
}

// ── 状态与渲染（一个表格容器，标签只决定用哪条描述）──

const state = { tab: 'api', rows: {}, selected: {} }

/** device_id → 设备名。数据来自 store.devices（顶栏每 30 秒刷新的共享状态）。 */
const deviceNames = new Map()

function renderRows() {
  const tab = TABS[state.tab]
  const rows = state.rows[state.tab] || []
  if (rows.length === 0) return '<div class="empty">暂无匹配记录</div>'
  const head = ['时间', ...tab.headers].map((title) => `<th>${esc(title)}</th>`).join('')
  const body = rows
    .map((row, index) => {
      const cells = tab
        .cells(row, deviceNames)
        .map((html) => `<td>${html}</td>`)
        .join('')
      const selected = index === state.selected[state.tab] ? ' is-selected' : ''
      return `<tr class="audit-row${selected}" data-row="${index}">
        <td class="mono">${esc(fmtFullDateTime(tab.time(row)))}</td>${cells}</tr>`
    })
    .join('')
  return `<table class="table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
}

function rerenderTable() {
  $('auditTable').innerHTML = renderRows()
}

function selectRow(index) {
  state.selected[state.tab] = index
  rerenderTable()
  const row = (state.rows[state.tab] || [])[index]
  const deviceSuffix = row?.device_id ? ` · ${deviceNames.get(row.device_id) || row.device_id}` : ''
  renderDetail(row, `${TABS[state.tab].label}详情${deviceSuffix}`)
}

/** 从共享状态重建设备名映射。store.devices 由顶栏每 30 秒刷新——这里只读它。 */
function refreshDeviceNames() {
  deviceNames.clear()
  for (const device of store.devices) deviceNames.set(device.device_id, device.name)
}

async function reload() {
  const scope = $('inScope').value
  const limit = Number($('inLimit').value) || 50
  const deviceId = scope === 'device' ? store.deviceId : ''
  const keys = Object.keys(TABS)
  try {
    // 三类各自取数、一起等待：加第四类只改 TABS，这里一行不动
    const loaded = await Promise.all(
      keys.map((key) => api.audit(TABS[key].kind, { device_id: deviceId, limit })),
    )
    refreshDeviceNames()
    keys.forEach((key, index) => {
      state.rows[key] = loaded[index]
      state.selected[key] = -1
    })
    rerenderTable()
    renderDetail(null, '')
    report(
      'auditResult',
      keys.map((key) => `${TABS[key].label} ${state.rows[key].length} 条`).join(' · '),
      'info',
    )
  } catch (error) {
    if (error?.status === 401) {
      report('auditResult', '身份缺失或未授权，请在顶栏填写身份标识', 'err')
      return
    }
    report('auditResult', problemText(error), 'err')
  }
}

let dispose = disposer()

export const auditView = {
  id: 'audit',
  title: '审计',

  mount(root) {
    root.innerHTML = `
      <div class="audit-split">
        <section class="panel">
          <div class="panel-head">
            ${Object.entries(TABS)
              .map(
                ([key, tab]) =>
                  `<span class="audit-tab${key === state.tab ? ' is-active' : ''}" data-tab="${key}" title="${esc(tab.title)}">${esc(tab.label)}</span>`,
              )
              .join('')}
            <span class="panel-note">点击行查看详情</span>
            <div class="panel-tools">
              <select id="inScope">
                <option value="all">全部设备</option>
                <option value="device">当前设备</option>
              </select>
              <input id="inLimit" type="number" min="1" max="500" value="50" style="width:90px">
              <button type="button" class="btn btn-ghost btn-sm" id="btnReload">刷新</button>
            </div>
          </div>
          <div class="panel-body flush" id="auditTable"></div>
        </section>

        <section class="panel" id="detailPanel">
          <div class="empty">点击左侧任一行，此处显示其详情</div>
        </section>
      </div>
      <div class="result" id="auditResult" style="padding:0 4px"></div>`

    dispose = disposer()

    const table = $('auditTable')
    dispose.add(
      listen(table, 'click', (event) => {
        const row = event.target.closest('[data-row]')
        if (row) selectRow(Number(row.dataset.row))
      }),
    )

    // 标签用委托：`[data-tab]` 上的 closest 会把 panel-head 里的下拉与按钮挡在外面
    dispose.add(
      listen(root.querySelector('.panel-head'), 'click', (event) => {
        const tab = event.target.closest('.audit-tab')
        if (!tab || state.tab === tab.dataset.tab) return
        state.tab = tab.dataset.tab
        state.selected = {}
        root.querySelectorAll('.audit-tab').forEach((item) => {
          item.classList.toggle('is-active', item.dataset.tab === state.tab)
        })
        rerenderTable()
        renderDetail(null, '')
      }),
    )

    const detailPanel = $('detailPanel')
    dispose.add(
      listen(detailPanel, 'click', (event) => {
        const card = event.target.closest('[data-copy-value]')
        if (card) {
          copy(card.dataset.copyValue, card.querySelector('.k')?.textContent || '值')
          return
        }
        if (event.target.closest('#btnCopyRow')) {
          const row = (state.rows[state.tab] || [])[state.selected[state.tab]]
          if (row) copy(JSON.stringify(row, null, 2), '整行 JSON')
        }
      }),
    )

    dispose.add(listen(root.querySelector('#btnReload'), 'click', reload))
    dispose.add(listen($('inScope'), 'change', reload))
    dispose.add(listen($('inLimit'), 'change', reload))

    // 设备清单是共享状态，由顶栏异步刷新——本视图切入时它可能还没到（刷新页面
    // 直接落在审计页时尤甚）。订阅它：清单一到/一变就更新名字映射并重渲染，
    // 否则名字时有时无取决于加载时序。
    dispose.add(
      subscribe('devices', () => {
        refreshDeviceNames()
        rerenderTable()
      }),
    )

    dispose.add(subscribe('view', (id) => { if (id !== 'audit') dispose.dispose() }))

    refreshDeviceNames()
    reload()
  },

  unmount() {
    dispose.dispose()
  },
}
