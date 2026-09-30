/**
 * 审计：服务端记录的命令流水。
 *
 * 与「概览」的事件日志不是一回事——那边是**本次浏览**看到的前端事件，刷新就没了；
 * 这里是服务端落的 `command_audit`，能按设备与条数查，用于回溯"几点几分下了什么、
 * 结果如何"。排障时先看这里，再看设备日志。
 *
 * 状态取值与设备端的回报一致：`ok` / `failed` / `rejected` / `timeout`。
 * `rejected` 是服务端自己拦下的（同时下发的命令太多），不是设备的问题。
 *
 * 动作名与原因码**照原样显示**：这是开发者页面，读它的人要拿这两个值去日志和代码里
 * 搜，翻译成中文反而搜不到。
 */
import { api } from '../core/api.js'
import { $, disposer, esc, listen } from '../core/dom.js'
import { statusBadgeClass, statusLabel } from '../core/outcome.js'
import { store, subscribe } from '../core/store.js'

function statusBadge(status) {
  return `<span class="badge ${statusBadgeClass(status)}">${esc(statusLabel(status))}</span>`
}

function report(text, kind) {
  const box = $('auditResult')
  box.className = `result ${kind}`
  box.textContent = text
}

function duration(row) {
  if (!row.finished_at || !row.created_at) return '—'
  return `${new Date(row.finished_at) - new Date(row.created_at)} ms`
}

function render(rows) {
  if (rows.length === 0) {
    $('auditTable').innerHTML = '<div class="empty">暂无匹配记录</div>'
    return
  }
  $('auditTable').innerHTML = `<table class="table"><thead><tr>
      <th>时间</th><th>设备</th><th>动作</th><th>状态</th><th>原因</th><th>耗时</th><th>命令</th>
    </tr></thead><tbody>${rows
      .map(
        (row) => `
      <tr>
        <td class="mono">${esc(new Date(row.created_at).toLocaleString('zh-CN', { hour12: false }))}</td>
        <td class="mono">${esc(row.device_id)}</td>
        <td class="mono">${esc(row.action)}</td>
        <td>${statusBadge(row.status)}</td>
        <td class="muted">${esc(row.reason_code || '—')}</td>
        <td class="mono">${esc(duration(row))}</td>
        <td class="muted mono" title="${esc(row.command_id)}">${esc(row.command_id.slice(0, 8))}</td>
      </tr>`,
      )
      .join('')}</tbody></table>`
}

/**
 * 拉一次。
 *
 * `scope` 取 `device` 时只查当前设备，取 `all` 时查全部——排障多半关心某一台，
 * 但"最近全系统发生了什么"也要能看。
 */
async function reload() {
  const scope = $('inScope').value
  const limit = Number($('inLimit').value) || 50
  try {
    const rows = await api.commands({
      device_id: scope === 'device' ? store.deviceId : '',
      limit,
    })
    render(rows)
    report(`共 ${rows.length} 条`, 'info')
  } catch (error) {
    report(error.message, 'err')
  }
}

let dispose = disposer()

export const auditView = {
  id: 'audit',
  title: '审计',

  mount(root) {
    root.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">命令流水</span>
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
      <div class="result" id="auditResult" style="padding:0 4px"></div>`

    dispose = disposer()
    dispose.add(subscribe('view', (id) => { if (id !== 'audit') dispose.dispose() }))
    dispose.add(listen($('btnReload'), 'click', reload))
    dispose.add(listen($('inScope'), 'change', reload))
    dispose.add(listen($('inLimit'), 'change', reload))

    reload()
  },

  unmount() {
    dispose.dispose()
  },
}
