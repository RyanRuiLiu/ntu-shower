/**
 * 身份：谁可以用这个系统、有什么能力、对哪些设备。
 *
 * ## 模型（能力 × 作用域）
 *
 * - **能力位**（capabilities）回答"能做什么"：ops（分配/结算）、photo（拍照）。
 *   清单由 `GET /api/v1/capabilities` 提供（服务端 Capability 枚举是唯一来源），
 *   新增能力位这里自动出现，不硬编码第二份。
 * - **设备作用域**（device_scope）回答"对哪些设备做"：`["*"]` 即全部设备。
 *   多选框数据来自 `/ops/devices`。
 *
 * ## 热更新
 *
 * 能力与作用域的改动**即时生效**：服务端每个请求实时查库，无缓存无重启。
 * 撤销同理——删掉即失去全部能力。
 *
 * 编辑入口：每行"编辑"进入行内编辑态（能力勾选 + 设备多选 + 备注），保存走
 * PUT（幂等登记）；签发新身份用表单。明文标识只在签发瞬间返回一次，之后
 * 不可再见——这是刻意的。
 */
import { api } from '../core/api.js'
import { problemText, report } from '../core/outcome.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

let capabilities = []
let devices = []
/** 行内编辑态的标识；null 表示不在编辑。 */
let editingIdentity = null

function capabilityBadges(values) {
  if (!values || values.length === 0) return '<span class="muted">无</span>'
  const byKey = Object.fromEntries(capabilities.map((cap) => [cap.key, cap.label]))
  return values.map((value) => `<span class="badge badge-blue">${esc(byKey[value] || value)}</span>`).join(' ')
}

function scopeText(scope) {
  if (!scope || scope.length === 0) return PLACEHOLDER
  if (scope.includes('*')) return '全部设备'
  return `${scope.length} 台`
}

function render(list) {
  if (list.length === 0) {
    $('identityTable').innerHTML = '<div class="empty">暂无身份记录</div>'
    return
  }
  const rows = list
    .map((item) => {
      if (editingIdentity === item.identity) return editorRow(item)
      return `<tr data-id="${esc(item.identity)}">
        <td><code>${esc(item.identity)}</code></td>
        <td>${esc(KIND_LABEL[item.kind] || item.kind)}</td>
        <td>${capabilityBadges(item.capabilities)}</td>
        <td title="${esc((item.device_scope || []).join(', '))}">${esc(scopeText(item.device_scope))}</td>
        <td>${esc(item.label || PLACEHOLDER)}</td>
        <td>
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="edit">编辑</button>
            <button type="button" class="btn btn-danger btn-sm" data-act="revoke">撤销</button>
          </div>
        </td>
      </tr>`
    })
    .join('')
  $('identityTable').innerHTML = `<table class="table"><thead><tr>
      <th>标识</th><th>来源</th><th>能力位</th><th>设备作用域</th><th>备注</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

/** 行内编辑态：能力勾选 + 设备多选 + 备注，保存走 PUT 幂等登记。 */
function editorRow(item) {
  const caps = capabilities
    .map((cap) => {
      const on = item.capabilities.includes(cap.key)
      return `<label class="field-row" style="margin:0 8px 0 0">
          <input type="checkbox" data-edit-cap="${esc(cap.key)}" ${on ? 'checked' : ''}>
          <span>${esc(cap.label)}</span>
        </label>`
    })
    .join('')
  const deviceBoxes = devices
    .map((device) => {
      const on = item.device_scope.includes('*') || item.device_scope.includes(device.device_id)
      const wildcard = item.device_scope.includes('*')
      return `<label class="field-row" style="margin:0 8px 0 0">
          <input type="checkbox" data-edit-device="${esc(device.device_id)}" ${on || wildcard ? 'checked' : ''} ${wildcard ? 'disabled' : ''}>
          <span>${esc(device.name || device.device_id)}</span>
        </label>`
    })
    .join('')
  const wildcardNote = item.device_scope.includes('*')
    ? '<div class="muted" style="margin:4px 0">该身份作用域为全部设备，如需限定请先取消「全部设备」保存后再编辑</div>'
    : ''
  return `<tr class="is-selected" data-id="${esc(item.identity)}">
      <td><code>${esc(item.identity)}</code></td>
      <td>${esc(KIND_LABEL[item.kind] || item.kind)}</td>
      <td colspan="2">
        <div class="field"><label>能力位</label><div class="btn-row">${caps}</div></div>
        <div class="field"><label>设备作用域</label><div class="btn-row">${deviceBoxes}</div>${wildcardNote}</div>
        <div class="field"><label>备注</label><input type="text" data-edit-label value="${esc(item.label || '')}"></div>
      </td>
      <td>
        <div class="btn-row">
          <button type="button" class="btn btn-sm" data-act="save-edit">保存</button>
          <button type="button" class="btn btn-ghost btn-sm" data-act="cancel-edit">取消</button>
        </div>
      </td>
    </tr>`
}

const KIND_LABEL = {
  openid: '小程序用户',
  web: '网页用户',
}

async function reload() {
  try {
    const [list, capList, deviceList] = await Promise.all([
      api.identities(),
      api.capabilities(),
      api.devices(),
    ])
    capabilities = capList
    devices = deviceList
    render(list)
  } catch (error) {
    report('identityResult', problemText(error), 'err')
  }
}

async function create() {
  const label = $('inLabel').value.trim()
  const capabilitiesSelected = [...$('capBox').querySelectorAll('input:checked')].map((input) => input.value)
  if (capabilitiesSelected.length === 0) {
    report('identityResult', '失败 · 至少选一个能力位', 'err')
    return
  }
  report('identityResult', '正在登记…', 'info')
  try {
    // kind=web：由服务端签发标识，明文只在本次响应返回，所以必须当场展示
    const created = await api.createIdentity({ kind: 'web', capabilities: capabilitiesSelected, label, openid: '' })
    report('identityResult', `已签发：${created.identity}（请立即记录，明文不会再次显示）`, 'ok')
    await reload()
  } catch (error) {
    report('identityResult', problemText(error), 'err')
  }
}

let dispose = disposer()

export const identitiesView = {
  id: 'identities',
  title: '身份',

  mount(root) {
    root.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">身份名单</span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnReload">刷新</button>
          </div>
        </div>
        <div class="panel-body flush" id="identityTable"></div>
      </section>

      <section class="panel">
        <div class="panel-head"><span class="panel-title">签发网页标识</span></div>
        <div class="panel-body">
          <div class="field">
            <label for="inLabel">备注（用途）</label>
            <input id="inLabel" type="text" placeholder="例如：运维值班">
          </div>
          <div class="field">
            <label>能力位</label>
            <div id="capBox"></div>
          </div>
          <div class="btn-row">
            <button type="button" class="btn btn-sm" id="btnCreate">登记</button>
          </div>
          <div class="result" id="identityResult"></div>
        </div>
      </section>`

    dispose = disposer()
    dispose.add(subscribe('view', (id) => { if (id !== 'identities') dispose.dispose() }))
    dispose.add(listen(root.querySelector('#btnReload'), 'click', reload))
    dispose.add(listen($('btnCreate'), 'click', create))

    // 列表事件委托：编辑/保存/取消/撤销
    dispose.add(
      listen($('identityTable'), 'click', async (event) => {
        const button = event.target.closest('button[data-act]')
        if (!button) return
        const identity = button.closest('tr').dataset.id
        const item = (await api.identities()).find((row) => row.identity === identity)
        if (button.dataset.act === 'edit') {
          editingIdentity = identity
          await reload()
          return
        }
        if (button.dataset.act === 'cancel-edit') {
          editingIdentity = null
          await reload()
          return
        }
        if (button.dataset.act === 'save-edit') {
          const row = button.closest('tr')
          const caps = [...row.querySelectorAll('[data-edit-cap]:checked')].map((input) => input.value)
          if (caps.length === 0) {
            report('identityResult', '失败 · 至少保留一个能力位', 'err')
            return
          }
          const scope = [...row.querySelectorAll('[data-edit-device]:checked')]
            .map((input) => input.dataset.editDevice)
          const label = row.querySelector('[data-edit-label]').value.trim()
          try {
            await api.updateIdentity(identity, { kind: 'web', capabilities: caps, device_scope: scope, label })
            toast('已更新（下一次请求即生效）')
            editingIdentity = null
            await reload()
          } catch (error) {
            report('identityResult', problemText(error), 'err')
          }
          return
        }
        if (button.dataset.act === 'revoke') {
          if (!window.confirm(`撤销 ${identity}？该标识立即失去全部能力。`)) return
          try {
            await api.deleteIdentity(identity)
            toast('已撤销')
            await reload()
          } catch (error) {
            report('identityResult', problemText(error), 'err')
          }
        }
      }),
    )

    // 渲染签发表单的能力位勾选（来自能力位注册接口，不硬编码）
    api.capabilities().then((capList) => {
      capabilities = capList
      $('capBox').innerHTML = capList
        .map(
          (cap) => `<label class="field-row" style="margin-bottom:4px">
            <input type="checkbox" value="${esc(cap.key)}">
            <span>${esc(cap.label)}</span>
          </label>`,
        )
        .join('')
    })

    reload()
  },

  unmount() {
    dispose.dispose()
  },
}
