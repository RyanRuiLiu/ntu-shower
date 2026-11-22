/**
 * 审计：接口调用（api_calls）、命令流水（command_audits）、后台任务（job_runs）、
 * 安装记录（ota_release_installs）、链路历史（device_link_events）。
 *
 * 五种审计结构不同，页面骨架却是同一个：一张关键列表 + 右侧全部字段。所以**差异只写在
 * [TABS] 里**（标题、取数、列头、每格怎么显示、时间取哪一列），渲染只有一份——加第六种
 * 审计就是加一条描述。早先每种审计各写一份表格渲染，两份几乎一样的代码各自演化；
 * 那不是风格问题，是"唯一实现"的问题。
 *
 * 表格只放关键列；点行选中，右侧详情面板常驻显示该行全部字段——详情在右栏常驻，
 * 比对几行时眼睛不用上下跑。详情面板遍历行对象的全部键渲染，后台加列自动出现，
 * 点值即复制，另有整行 JSON 复制。标签与行都能用键盘操作：标签间用方向键，
 * 行组只有一个 Tab 停靠点，行内用方向键移动、Enter 或空格查看详情。
 *
 * 动作名、原因码、功能名、结果名一律**照原样显示**：这是开发者页面，读它的人要拿这些值
 * 去日志里搜（`job.settled`、`job=renew_wait`），翻译成中文反而搜不到。
 */
import { api } from '../core/api.js'
import { copyText } from '../core/clipboard.js'
import { codeCell, deviceCell } from '../core/cells.js'
import { fmtFullDateTime } from '../core/clock.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { problemText, report, statusBadgeClass, statusLabel } from '../core/outcome.js'
import { deviceName, store, subscribe } from '../core/store.js'

function statusBadge(status) {
  if (status === null || status === undefined) return PLACEHOLDER
  return `<span class="badge ${statusBadgeClass(status)}">${esc(statusLabel(status))}</span>`
}

/** 失败进本页结果区（核心模块不认识各页的结果区），成功文案由核心统一。 */
function copy(text, what) {
  copyText(text, { label: what, onFail: () => report('auditResult', '复制失败（浏览器未授予剪贴板权限）', 'err') })
}

/** 详情卡：标签 + 全量折行的值，点卡即复制。空值不渲染。按钮元素让键盘同样能触发。 */
function detailCard(key, value) {
  if (value === null || value === undefined || value === '') return ''
  const text = String(value)
  return `<button type="button" class="detail-card" data-copy-value="${esc(text)}" title="点击复制">
      <span class="k">${esc(key)}</span>
      <span class="v mono">${esc(text)}</span>
    </button>`
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
    source: 'audit',
    time: (row) => row.requested_at,
    headers: ['调用方', '请求', '状态', '耗时', '问题'],
    cells: (row) => [
      `${mono(row.actor_kind)}${
        row.client_ip ? `<div class="muted" title="来源 IP">${esc(row.client_ip)}</div>` : ''
      }`,
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
    source: 'audit',
    time: (row) => row.created_at,
    headers: ['设备', '动作', '状态', '原因', '耗时'],
    cells: (row) => [
      deviceCell(row.device_id),
      mono(row.action),
      statusBadge(row.status),
      muted(row.reason_code),
      mono(row.finished_at ? `${new Date(row.finished_at) - new Date(row.created_at)} ms` : PLACEHOLDER),
    ],
  },
  job: {
    label: '后台任务',
    title: '配置好的后台任务自动替谁做了什么、为什么（时间与命令流水对照即知发了哪条命令）',
    kind: 'job-runs',
    source: 'audit',
    time: (row) => row.occurred_at,
    headers: ['设备', '喷头', '学号', '功能', '动作', '结果', '阈值', '原因'],
    cells: (row) => [
      deviceCell(row.device_id),
      row.door_no === null ? PLACEHOLDER : mono(row.door_no),
      row.cert_no === null ? PLACEHOLDER : codeCell(row.cert_no),
      mono(row.job),
      mono(row.action),
      mono(row.outcome),
      row.threshold_s === null ? PLACEHOLDER : mono(`${row.threshold_s} 秒`),
      muted(row.reason),
    ],
  },
  install: {
    label: '安装记录',
    title: '逐设备的版本安装确认：哪台设备在何时装上了哪个版本',
    source: 'installs',
    time: (row) => row.applied_at,
    headers: ['设备', '版本'],
    cells: (row) => [deviceCell(row.device_id), mono(row.version)],
  },
  link: {
    label: '链路历史',
    title: '设备的上线与下线事件（全队，或按上方设备过滤看单台）',
    source: 'link',
    time: (row) => row.occurred_at,
    headers: ['设备', '事件', '原因'],
    cells: (row) => [deviceCell(row.device_id), mono(row.event), muted(row.reason)],
  },
}

// ── 状态与渲染（一个表格容器，标签只决定用哪条描述）──

const state = { tab: 'api', rows: {}, selected: {} }

function renderRows() {
  const tab = TABS[state.tab]
  const rows = state.rows[state.tab] || []
  if (rows.length === 0) return '<div class="empty">暂无匹配记录；调整筛选条件</div>'
  const head = ['时间', ...tab.headers].map((title) => `<th>${esc(title)}</th>`).join('')
  // 行组只有一个 Tab 停靠点（选中行，未选时是第一行）：几百行不该变成几百个 Tab。
  // 组内移动用方向键，选中用 Enter 或空格。
  const selectedIndex = state.selected[state.tab]
  const tabStop = selectedIndex >= 0 && selectedIndex < rows.length ? selectedIndex : 0
  const body = rows
    .map((row, index) => {
      const cells = tab
        .cells(row)
        .map((html) => `<td>${html}</td>`)
        .join('')
      const selected = index === selectedIndex
      return `<tr class="audit-row${selected ? ' is-selected' : ''}" data-row="${index}"
        tabindex="${index === tabStop ? 0 : -1}" aria-selected="${selected}">
        <td class="mono">${esc(fmtFullDateTime(tab.time(row)))}</td>${cells}</tr>`
    })
    .join('')
  return `<table class="table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
}

/**
 * 重画表格。行是键盘可聚焦的，整表重画会丢掉焦点与滚动位置——两者都要按重画前
 * 的状态还原，否则用方向键翻看时列表会跳回顶部。
 */
function rerenderTable() {
  const box = $('auditTable')
  const scrollTop = box.scrollTop
  const focusedRow = box.contains(document.activeElement)
    ? document.activeElement.closest('[data-row]')?.dataset.row
    : undefined
  box.innerHTML = renderRows()
  box.scrollTop = scrollTop
  if (focusedRow !== undefined) box.querySelector(`[data-row="${focusedRow}"]`)?.focus()
}

function selectRow(index, { focus = false } = {}) {
  state.selected[state.tab] = index
  rerenderTable()
  if (focus) $('auditTable').querySelector(`[data-row="${index}"]`)?.focus()
  const row = (state.rows[state.tab] || [])[index]
  const deviceSuffix = row?.device_id ? ` · ${deviceName(row.device_id)}` : ''
  renderDetail(row, `${TABS[state.tab].label}详情${deviceSuffix}`)
}

/** 切到某个审计类别：按钮状态、表格内容与详情面板一起换。 */
function activateTab(root, key) {
  if (state.tab === key) return
  state.tab = key
  state.selected = {}
  root.querySelectorAll('.audit-tab').forEach((item) => {
    const active = item.dataset.tab === key
    item.classList.toggle('is-active', active)
    item.setAttribute('aria-selected', String(active))
    item.tabIndex = active ? 0 : -1
  })
  $('auditTable').setAttribute('aria-labelledby', `auditTab-${key}`)
  rerenderTable()
  renderDetail(null, '')
}

async function reload() {
  const scope = $('inScope').value
  const limit = Number($('inLimit').value) || 50
  const deviceId = scope === 'device' ? store.deviceId : ''
  const keys = Object.keys(TABS)
  try {
    // 各类各自取数、一起等待：加一类只改 TABS，这里只挑取数方式。审计三类走
    // /audit/{kind}；安装记录挂在 /ota/installs（发布域）；链路历史挂在设备域、
    // 与上方设备过滤同档（全队或单台），行内 device_id 回答"是哪台设备"。
    const auditKeys = keys.filter((key) => TABS[key].source === 'audit')
    const [loaded, installs, linkRows] = await Promise.all([
      Promise.all(auditKeys.map((key) => api.audit(TABS[key].kind, { device_id: deviceId, limit }))),
      api.otaInstalls({ device_id: deviceId, limit }),
      api.linkEvents({ device_id: deviceId, limit }),
    ])
    const loadedByKey = new Map(auditKeys.map((key, index) => [key, loaded[index]]))
    keys.forEach((key) => {
      state.rows[key] =
        TABS[key].source === 'link' ? linkRows : TABS[key].source === 'installs' ? installs : loadedByKey.get(key)
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
      report('auditResult', '未登录或会话已过期，请在顶栏登录', 'err')
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
            <div class="audit-tabs" role="tablist" aria-label="审计类别">
              ${Object.entries(TABS)
                .map(
                  ([key, tab]) =>
                    `<button type="button" class="audit-tab${key === state.tab ? ' is-active' : ''}"
                      id="auditTab-${key}" role="tab" data-tab="${key}"
                      aria-selected="${key === state.tab}" aria-controls="auditTable"
                      tabindex="${key === state.tab ? 0 : -1}" title="${esc(tab.title)}">${esc(tab.label)}</button>`,
                )
                .join('')}
            </div>
            <span class="panel-note">点击或回车查看详情</span>
            <div class="panel-tools">
              <select id="inScope" aria-label="审计范围">
                <option value="all">全部设备</option>
                <option value="device">当前设备</option>
              </select>
              <input id="inLimit" type="number" min="1" max="500" value="50" aria-label="审计条数上限">
              <button type="button" class="btn btn-ghost btn-sm" id="btnReload">刷新</button>
            </div>
          </div>
          <div class="panel-body flush" id="auditTable" role="tabpanel"
            aria-labelledby="auditTab-${state.tab}"></div>
        </section>

        <section class="panel" id="detailPanel">
          <div class="empty">点击左侧任一行，此处显示其详情</div>
        </section>
      </div>
      <div class="panel-body"><div class="result" id="auditResult"></div></div>`

    dispose = disposer()

    const table = $('auditTable')
    dispose.add(
      listen(table, 'click', (event) => {
        const row = event.target.closest('[data-row]')
        if (row) selectRow(Number(row.dataset.row))
      }),
    )

    // 行组只有一个 Tab 停靠点：方向键在行间移动，Enter 与空格等同点击
    dispose.add(
      listen(table, 'keydown', (event) => {
        const row = event.target.closest('[data-row]')
        if (!row) return
        const rows = [...table.querySelectorAll('[data-row]')]
        const position = rows.indexOf(row)
        let next = -1
        if (event.key === 'ArrowDown') next = Math.min(position + 1, rows.length - 1)
        else if (event.key === 'ArrowUp') next = Math.max(position - 1, 0)
        else if (event.key === 'Home') next = 0
        else if (event.key === 'End') next = rows.length - 1
        else if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          selectRow(Number(row.dataset.row), { focus: true })
          return
        }
        if (next < 0) return
        event.preventDefault()
        rows.forEach((item) => {
          item.tabIndex = -1
        })
        rows[next].tabIndex = 0
        rows[next].focus()
      }),
    )

    // 标签用委托：`[data-tab]` 上的 closest 会把 panel-head 里的下拉与按钮挡在外面。
    // 按钮原生响应 Enter/空格；方向键与 Home/End 按标签组的惯例在标签间移动。
    const tabList = root.querySelector('[role="tablist"]')
    dispose.add(
      listen(tabList, 'click', (event) => {
        const tab = event.target.closest('.audit-tab')
        if (tab) activateTab(root, tab.dataset.tab)
      }),
    )
    dispose.add(
      listen(tabList, 'keydown', (event) => {
        const keys = Object.keys(TABS)
        const current = keys.indexOf(state.tab)
        let next = -1
        if (event.key === 'ArrowRight') next = (current + 1) % keys.length
        else if (event.key === 'ArrowLeft') next = (current - 1 + keys.length) % keys.length
        else if (event.key === 'Home') next = 0
        else if (event.key === 'End') next = keys.length - 1
        if (next < 0) return
        event.preventDefault()
        activateTab(root, keys[next])
        tabList.querySelector(`[data-tab="${keys[next]}"]`)?.focus()
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
    // 直接落在审计页时尤甚）。订阅它：清单一到/一变就重渲染，否则设备名时有时无
    // 取决于加载时序。
    dispose.add(subscribe('devices', rerenderTable))

    dispose.add(subscribe('view', (id) => { if (id !== 'audit') dispose.dispose() }))

    reload()
  },

  unmount() {
    dispose.dispose()
  },
}
