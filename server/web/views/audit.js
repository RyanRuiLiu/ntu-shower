/**
 * 审计：接口调用审计（api_call）与命令流水（command_audit），两层同屏。
 *
 * 排障的读法：接口审计回答"谁在几点调了什么、结果如何"；命令流水回答"这条命令
 * 下发到设备后成了没有"。两表以 `trace_id` 关联——接口行与它派发的命令行各带一份。
 *
 * 展示原则：**库里有什么就展示什么**，不做摘要化裁剪。单行放不下的字段
 * （request_id / trace_id 全值、失败说明 detail）收进行展开区，点行展开；
 * 每行可整行复制为 JSON，带出去比对时不用手抄。
 *
 * 设备显示名字（名字是人在意的），id 放在展开区与 title 里。
 *
 * 动作名与原因码**照原样显示**：这是开发者页面，读它的人要拿这两个值去日志和
 * 代码里搜，翻译成中文反而搜不到。
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

/** 复制文本；审计页的复制是高频动作，成功失败都要有反馈。 */
function copy(text, what) {
  navigator.clipboard.writeText(text).then(
    () => toast(`已复制${what}`),
    () => report('auditResult', '复制失败（浏览器未授予剪贴板权限）', 'err'),
  )
}

/** 展开区里的一项：键 + 值（值全量展示，长值折行）。 */
function detailItem(key, value) {
  if (value === null || value === undefined || value === '') return ''
  return `<div class="kv"><span class="k">${esc(key)}</span><span class="v mono">${esc(value)}</span></div>`
}

// ── 接口调用审计（api_call）──

function apiRowHtml(row, index, deviceNames) {
  const device = row.device_id ? deviceNames.get(row.device_id) || row.device_id : PLACEHOLDER
  const methodBadge =
    row.kind === 'gateway' ? `<span class="badge badge-blue">网关 ${row.status_code === 0 ? '连接' : '断开'}</span>` : esc(row.method)
  return `<tr class="node is-clickable" data-api="${index}" title="点击展开全部字段">
      <td class="mono">${esc(fmtFullDateTime(row.requested_at))}</td>
      <td>${methodBadge}</td>
      <td class="mono">${esc(row.actor_kind)}<span class="muted" title="${esc(row.actor)}">/${esc(row.actor.slice(0, 8))}</span></td>
      <td title="${esc(row.device_id || '')}">${esc(device)}</td>
      <td class="mono">${esc(row.method)} ${esc(row.path)}${row.query ? `<span class="muted">?${esc(row.query)}</span>` : ''}</td>
      <td class="mono">${row.status_code}</td>
      <td class="mono">${row.duration_ms} ms</td>
      <td class="muted" title="${esc(row.problem_type || '')}">${esc(row.problem_type || PLACEHOLDER)}</td>
    </tr>
    <tr class="detail-row" data-detail-for="${index}" hidden><td colspan="8">
      <div class="detail-block">
        ${detailItem('requested_at', row.requested_at)}
        ${detailItem('request_id', row.request_id)}
        ${detailItem('trace_id', row.trace_id)}
        ${detailItem('actor', row.actor_kind === 'ops' ? `${row.actor_kind}（令牌指纹）` : row.actor)}
        ${detailItem('device_id', row.device_id)}
        ${detailItem('problem_type', row.problem_type)}
        ${detailItem('detail', row.detail)}
        <button type="button" class="btn btn-ghost btn-sm" data-copy-api="${index}">复制整行 JSON</button>
      </div>
    </td></tr>`
}

function renderApiCalls(rows, deviceNames) {
  if (rows.length === 0) {
    $('apiAuditTable').innerHTML = '<div class="empty">暂无匹配记录</div>'
    return
  }
  $('apiAuditTable').innerHTML = `<table class="table"><thead><tr>
      <th>时间</th><th>类别</th><th>调用方</th><th>设备</th><th>请求</th><th>状态</th><th>耗时</th><th>问题</th>
    </tr></thead><tbody>${rows.map((row, index) => apiRowHtml(row, index, deviceNames)).join('')}</tbody></table>`
}

// ── 命令流水（command_audit）──

function commandRowHtml(row, index, deviceNames) {
  const device = deviceNames.get(row.device_id) || row.device_id
  const elapsed = row.finished_at && row.created_at ? `${new Date(row.finished_at) - new Date(row.created_at)} ms` : PLACEHOLDER
  return `<tr class="node is-clickable" data-cmd="${index}" title="点击展开全部字段">
      <td class="mono">${esc(fmtFullDateTime(row.created_at))}</td>
      <td title="${esc(row.device_id)}">${esc(device)}</td>
      <td class="mono">${esc(row.action)}</td>
      <td>${statusBadge(row.status)}</td>
      <td class="muted">${esc(row.reason_code || PLACEHOLDER)}</td>
      <td class="mono">${esc(elapsed)}</td>
    </tr>
    <tr class="detail-row" data-detail-for="cmd-${index}" hidden><td colspan="6">
      <div class="detail-block">
        ${detailItem('command_id', row.command_id)}
        ${detailItem('trace_id', row.trace_id)}
        ${detailItem('device_id', row.device_id)}
        ${detailItem('finished_at', row.finished_at)}
        ${detailItem('说明', row.terminal_message)}
        <button type="button" class="btn btn-ghost btn-sm" data-copy-cmd="${index}">复制整行 JSON</button>
      </div>
    </td></tr>`
}

function renderCommands(rows, deviceNames) {
  if (rows.length === 0) {
    $('commandTable').innerHTML = '<div class="empty">暂无匹配记录</div>'
    return
  }
  $('commandTable').innerHTML = `<table class="table"><thead><tr>
      <th>时间</th><th>设备</th><th>动作</th><th>状态</th><th>原因</th><th>耗时</th>
    </tr></thead><tbody>${rows.map((row, index) => commandRowHtml(row, index, deviceNames)).join('')}</tbody></table>`
}

// ── 数据与装配 ──

let apiRows = []
let commandRows = []
const deviceNames = new Map()

async function reload() {
  const scope = $('inScope').value
  const limit = Number($('inLimit').value) || 50
  const deviceId = scope === 'device' ? store.deviceId : ''
  try {
    const [devices, calls, commands] = await Promise.all([
      api.devices(),
      api.auditCalls({ device_id: deviceId, limit }),
      api.commands({ device_id: deviceId, limit }),
    ])
    deviceNames.clear()
    for (const device of devices) deviceNames.set(device.device_id, device.name)
    apiRows = calls.calls
    commandRows = commands
    renderApiCalls(apiRows, deviceNames)
    renderCommands(commandRows, deviceNames)
    report(`接口 ${apiRows.length} 条 · 命令 ${commandRows.length} 条`, 'info')
  } catch (error) {
    report('auditResult', problemText(error), 'err')
  }
}

function toggleDetail(table, key) {
  const detailRow = table.querySelector(`[data-detail-for="${key}"]`)
  if (!detailRow) return
  detailRow.hidden = !detailRow.hidden
}

let dispose = disposer()

export const auditView = {
  id: 'audit',
  title: '审计',

  mount(root) {
    root.innerHTML = `
      <div class="panel-tools" style="padding:0 4px">
        <select id="inScope">
          <option value="device">当前设备</option>
          <option value="all">全部设备</option>
        </select>
        <input id="inLimit" type="number" min="1" max="500" value="50" style="width:90px">
        <button type="button" class="btn btn-ghost btn-sm" id="btnReload">刷新</button>
        <span class="muted">点行展开全部字段；展开区可整行复制 JSON</span>
      </div>

      <section class="panel">
        <div class="panel-head"><span class="panel-title">接口调用审计</span></div>
        <div class="panel-body flush" id="apiAuditTable"></div>
      </section>

      <section class="panel">
        <div class="panel-head"><span class="panel-title">命令流水</span></div>
        <div class="panel-body flush" id="commandTable"></div>
      </section>
      <div class="result" id="auditResult" style="padding:0 4px"></div>`

    dispose = disposer()
    dispose.add(subscribe('view', (id) => { if (id !== 'audit') dispose.dispose() }))
    dispose.add(listen(root.querySelector('#btnReload'), 'click', reload))
    dispose.add(listen($('inScope'), 'change', reload))
    dispose.add(listen($('inLimit'), 'change', reload))

    // 行点击 = 展开收起详情；展开区里的复制按钮按行取 JSON
    const apiTable = $('apiAuditTable')
    dispose.add(
      listen(apiTable, 'click', (event) => {
        const copyButton = event.target.closest('[data-copy-api]')
        if (copyButton) {
          event.stopPropagation()
          copy(JSON.stringify(apiRows[Number(copyButton.dataset.copyApi)], null, 2), '接口审计行')
          return
        }
        const row = event.target.closest('[data-api]')
        if (row) toggleDetail(apiTable, row.dataset.api)
      }),
    )
    const commandTable = $('commandTable')
    dispose.add(
      listen(commandTable, 'click', (event) => {
        const copyButton = event.target.closest('[data-copy-cmd]')
        if (copyButton) {
          event.stopPropagation()
          copy(JSON.stringify(commandRows[Number(copyButton.dataset.copyCmd)], null, 2), '命令审计行')
          return
        }
        const row = event.target.closest('[data-cmd]')
        if (row) toggleDetail(commandTable, row.dataset.cmd)
      }),
    )

    reload()
  },

  unmount() {
    dispose.dispose()
  },
}
