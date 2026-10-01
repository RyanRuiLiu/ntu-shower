/**
 * 审计：接口调用审计（api_call）与命令流水（command_audit），标签切换、主从式。
 *
 * ## 交互：标签切换 + 选中行看详情
 *
 * 两个数据源是**两种审计**，用标签切换而不是上下堆两张表——堆叠会把页面撑得很长，
 * 且看一个表时另一个在视野里纯属干扰：
 * - **接口调用**：每一次后台请求——谁、从哪个 IP、调了什么、结果如何。查"有人
 *   动过什么、从哪里动的"来这里；
 * - **命令流水**：每条发到设备的命令——参数、结果、设备回报。查"设备执行得
 *   怎么样"来这里。
 *
 * 表格只放关键列；**点行 = 选中**（高亮），
 * 右侧详情面板随即显示该行的全部字段——详情在右栏**常驻**而不是页面底部：
 * 比对几行时眼睛不用上下跑。不做"点行展开/再点收起"——那个交互点击
 * 目标小、表格忽长忽短、字段藏在点击之后，连续比对几行会非常累。
 *
 * ## 展示：库里有什么就展示什么
 *
 * 详情面板直接遍历行对象的**全部键**渲染——后台加列这里自动出现，不存在
 * "表格挑过几列、其余看不到"的摘要化损失。每个字段一张小卡：标签 + 全量折行的值，
 * **点值即复制**；另有整行 JSON 复制，带出去比对不用手抄。
 *
 * ## 残留与冲突的防位
 *
 * 只有一个表格容器（`#auditTable`），切换标签时整体重渲染——没有"隐藏的表"留着
 * 旧 DOM；详情面板内容随选中整体重建，事件监听用面板级代理只挂一次。视图切走时
 * 全部监听经 dispose 摘除。
 *
 * 设备显示名字（名字是人在意的），id 在详情卡里。动作名与原因码**照原样显示**：
 * 这是开发者页面，读它的人要拿这两个值去日志和代码里搜，翻译成中文反而搜不到。
 */
import { api } from '../core/api.js'
import { fmtFullDateTime } from '../core/clock.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { problemText, report, statusBadgeClass, statusLabel } from '../core/outcome.js'
import { store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

/** 标签定义：单一事实来源——按钮、标题（各自记什么）、取数都从这里走。 */
const TABS = {
  api: { label: '接口调用', title: '每一次后台请求：谁、从哪个 IP、调了什么、结果如何' },
  command: { label: '命令流水', title: '每条发到设备的命令：参数、结果、设备回报' },
}

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
    panel.innerHTML = '<div class="empty">点击上方任一行查看全部字段（点字段值可复制）</div>'
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

function renderApiRows(rows, deviceNames, selectedIndex) {
  if (rows.length === 0) return '<div class="empty">暂无匹配记录</div>'
  return `<table class="table"><thead><tr>
      <th>时间</th><th>类别</th><th>调用方</th><th>设备</th><th>请求</th><th>状态</th><th>耗时</th><th>问题</th>
    </tr></thead><tbody>${rows
      .map((row, index) => {
        const device = row.device_id ? deviceNames.get(row.device_id) || row.device_id : PLACEHOLDER
        const kindBadge =
          row.kind === 'gateway'
            ? `<span class="badge badge-blue">网关${row.status_code === 0 ? '连接' : '断开'}</span>`
            : esc(row.method)
        const selected = index === selectedIndex ? ' is-selected' : ''
        return `<tr class="audit-row${selected}" data-row="${index}">
        <td class="mono">${esc(fmtFullDateTime(row.requested_at))}</td>
        <td>${kindBadge}</td>
        <td class="mono">${esc(row.actor_kind)}${row.client_ip ? `<div class="muted" title="来源 IP">${esc(row.client_ip)}</div>` : ''}</td>
        <td title="${esc(row.device_id || '')}">${esc(device)}</td>
        <td class="mono">${esc(row.method)} ${esc(row.path)}${row.query ? `<span class="muted">?${esc(row.query)}</span>` : ''}</td>
        <td class="mono">${row.status_code}</td>
        <td class="mono">${row.duration_ms} ms</td>
        <td class="muted">${esc(row.problem_type || PLACEHOLDER)}</td>
      </tr>`
      })
      .join('')}</tbody></table>`
}

function renderCommandRows(rows, deviceNames, selectedIndex) {
  if (rows.length === 0) return '<div class="empty">暂无匹配记录</div>'
  return `<table class="table"><thead><tr>
      <th>时间</th><th>设备</th><th>动作</th><th>状态</th><th>原因</th><th>耗时</th>
    </tr></thead><tbody>${rows
      .map((row, index) => {
        const device = deviceNames.get(row.device_id) || row.device_id
        const elapsed = row.finished_at && row.created_at ? `${new Date(row.finished_at) - new Date(row.created_at)} ms` : PLACEHOLDER
        const selected = index === selectedIndex ? ' is-selected' : ''
        return `<tr class="audit-row${selected}" data-row="${index}">
        <td class="mono">${esc(fmtFullDateTime(row.created_at))}</td>
        <td title="${esc(row.device_id)}">${esc(device)}</td>
        <td class="mono">${esc(row.action)}</td>
        <td>${statusBadge(row.status)}</td>
        <td class="muted">${esc(row.reason_code || PLACEHOLDER)}</td>
        <td class="mono">${esc(elapsed)}</td>
      </tr>`
      })
      .join('')}</tbody></table>`
}

// ── 状态与渲染（单一表格容器，标签决定渲染哪份数据）──

const state = {
  tab: 'api',
  rows: { api: [], command: [] },
  selected: { api: -1, command: -1 },
}

/** device_id → 设备名。数据来自 store.devices（顶栏每 30 秒刷新的共享状态）。 */
const deviceNames = new Map()

function rerenderTable() {
  const table = $('auditTable')
  if (state.tab === 'api') {
    table.innerHTML = renderApiRows(state.rows.api, deviceNames, state.selected.api)
  } else {
    table.innerHTML = renderCommandRows(state.rows.command, deviceNames, state.selected.command)
  }
}

function selectRow(index) {
  state.selected[state.tab] = index
  const other = state.tab === 'api' ? 'command' : 'api'
  state.selected[other] = -1
  rerenderTable()
  const row = state.rows[state.tab][index]
  const deviceSuffix = row?.device_id ? ` · ${deviceNames.get(row.device_id) || row.device_id}` : ''
  renderDetail(row, `${TABS[state.tab].label}详情${deviceSuffix}`)
}

async function reload() {
  const scope = $('inScope').value
  const limit = Number($('inLimit').value) || 50
  const deviceId = scope === 'device' ? store.deviceId : ''
  try {
    // 设备名映射用 store.devices（顶栏每 30 秒刷新的共享状态）——自己再拉一份
    // 就是同一份数据两个来源，迟早打架
    const [calls, commands] = await Promise.all([
      api.auditCalls({ device_id: deviceId, limit }),
      api.commands({ device_id: deviceId, limit }),
    ])
    deviceNames.clear()
    for (const device of store.devices) deviceNames.set(device.device_id, device.name)
    state.rows.api = calls.calls
    state.rows.command = commands
    state.selected.api = -1
    state.selected.command = -1
    rerenderTable()
    renderDetail(null, '')
    report('auditResult', `接口 ${state.rows.api.length} 条 · 命令 ${state.rows.command.length} 条`, 'info')
  } catch (error) {
    if (error?.status === 401) {
      report('auditResult', '访问令牌缺失或无效，请在顶栏填写', 'err')
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
            <span class="panel-note">点行看详情</span>
            <div class="panel-tools">
              <select id="inScope">
                <option value="device">当前设备</option>
                <option value="all">全部设备</option>
              </select>
              <input id="inLimit" type="number" min="1" max="500" value="50" style="width:90px">
              <button type="button" class="btn btn-ghost btn-sm" id="btnReload">刷新</button>
            </div>
          </div>
          <div class="panel-body flush" id="auditTable"></div>
        </section>

        <section class="panel" id="detailPanel">
          <div class="empty">点击左侧任一行，详情显示在这里</div>
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

    const detailPanel = $('detailPanel')
    dispose.add(
      listen(detailPanel, 'click', (event) => {
        const card = event.target.closest('[data-copy-value]')
        if (card) {
          copy(card.dataset.copyValue, card.querySelector('.k')?.textContent || '值')
          return
        }
        if (event.target.closest('#btnCopyRow')) {
          const row = state.rows[state.tab][state.selected[state.tab]]
          if (row) copy(JSON.stringify(row, null, 2), '整行 JSON')
        }
      }),
    )

    root.querySelectorAll('.audit-tab').forEach((tab) => {
      dispose.add(
        listen(tab, 'click', () => {
          if (state.tab === tab.dataset.tab) return
          state.tab = tab.dataset.tab
          state.selected = { api: -1, command: -1 }
          root.querySelectorAll('.audit-tab').forEach((item) => {
            item.classList.toggle('is-active', item.dataset.tab === state.tab)
          })
          rerenderTable()
          renderDetail(null, '')
        }),
      )
    })

    dispose.add(listen(root.querySelector('#btnReload'), 'click', reload))
    dispose.add(listen($('inScope'), 'change', reload))
    dispose.add(listen($('inLimit'), 'change', reload))

    dispose.add(subscribe('view', (id) => { if (id !== 'audit') dispose.dispose() }))

    reload()
  },

  unmount() {
    dispose.dispose()
  },
}
