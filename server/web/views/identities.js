/**
 * 身份：谁可以用这个系统、有什么能力、对哪些设备。
 *
 * ## 模型（能力位 × 作用域）
 *
 * - **能力位**（capabilities）回答"能做什么"：session（分配/结算）、photo（拍照）、
 *   admin（管理台）、tasks（后台任务名单维护）。清单来自 `GET /identities` 的域读模型（服务端 Capability
 *   枚举是唯一来源），不硬编码第二份。
 * - **设备作用域**（device_scope）回答"对哪些设备做"：`["*"]` 即全部设备，否则
 *   是设备 id 列表。开关在两种模式间切换，勾选与展示一律用设备名；登记与编辑
 *   共用同一形状（core/device-scope.js，与语音页的规则目标同一份实现）。
 *
 * ## 热更新
 *
 * 能力与作用域的改动即时生效：服务端每个请求实时查库，无缓存无重启。撤销同理。
 *
 * ## 编辑
 *
 * 编辑不在表格行内进行：行内控件会把该行撑高、迫使全表列宽重排，名单越长跳动
 * 越明显。表格只作展示，点「编辑」把该行值填入下方表单（登记/编辑双模式）并滚动
 * 过去；保存走 PUT，标识与来源不变。
 *
 * ## 签发
 *
 * kind 二选一：web（自定义标识或留空由服务端生成，明文只在签发响应出现一次，
 * 需显式确认已保存）与 openid（登记小程序用户的 openid）。
 */
import { api } from '../core/api.js'
import { problemText, report } from '../core/outcome.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { bindScopeToggle, readScopeEditor, scopeEditorHtml } from '../core/device-scope.js'
import { deviceName, refreshDevices, store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

let capabilities = []
let identities = []
/** 编辑态：正在编辑的标识与其来源类型（PUT 需回传 kind）；null 即登记模式。 */
let editingIdentity = null
let editingKind = 'web'

const KIND_LABEL = {
  openid: '小程序用户',
  web: '网页用户',
}

function scopeText(scope) {
  if (!scope || scope.length === 0) return PLACEHOLDER
  if (scope.includes('*')) return '全部设备'
  return scope.map(deviceName).join('、')
}

function capabilityBadges(values) {
  if (!values || values.length === 0) return '<span class="muted">无</span>'
  const byKey = Object.fromEntries(capabilities.map((cap) => [cap.key, cap.label]))
  return values.map((value) => `<span class="badge badge-blue">${esc(byKey[value] || value)}</span>`).join(' ')
}

/** 容器内勾选的能力位集合（勾选框不带 value，键在 data-cap 上）。 */
function readCapabilityChecks(container) {
  return [...container.querySelectorAll('input[data-cap]:checked')].map((input) => input.dataset.cap)
}

/** 能力位勾选组（登记与编辑共用）。 */
function capabilityChecksHtml(selected) {
  const checks = capabilities
    .map((cap) => {
      const on = selected.includes(cap.key)
      return `<label class="field-row">
          <input type="checkbox" data-cap="${esc(cap.key)}" ${on ? 'checked' : ''}>
          <span>${esc(cap.label)}</span>
        </label>`
    })
    .join('')
  return `<div class="btn-row">${checks}</div>`
}

function render(list) {
  $('identityCount').textContent = list.length > 0 ? `共 ${list.length} 条` : ''
  if (list.length === 0) {
    $('identityTable').innerHTML = '<div class="empty">暂无身份记录</div>'
    return
  }
  const rows = list
    .map((item) => {
      // 正在编辑的行标出选中态：只换背景色，不动结构，列宽与行高保持不变
      const selected = editingIdentity === item.identity ? ' class="is-selected"' : ''
      return `<tr${selected} data-id="${esc(item.identity)}">
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
      <th>身份标识</th><th>来源</th><th>能力位</th><th>设备作用域</th><th>备注</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

async function reload() {
  try {
    // 设备清单经 store 的 setter 拉取：作用域编辑器与 deviceName 都读 store.devices
    const [bundle] = await Promise.all([api.identities(), refreshDevices()])
    capabilities = bundle.capabilities
    identities = bundle.identities
    render(identities)
    if (editingIdentity !== null) {
      // 编辑中刷新：以清单现值重填表单；该标识已不存在则回到登记模式
      const item = identities.find((row) => row.identity === editingIdentity)
      if (item) fillForm(item)
      else exitEdit()
    } else {
      clearForm()
    }
  } catch (error) {
    report('identityResult', problemText(error), 'err')
  }
}

/** 登记模式下当前选中的 kind。 */
function currentKind() {
  return $('inKindWeb').checked ? 'web' : 'openid'
}

/**
 * 表单双模式切换：登记（POST 签发）与编辑（PUT 更新）共用同一组控件。
 *
 * 编辑时来源类型不可改（PUT 需回传原 kind），标识与 openid 输入框随之隐藏——
 * 标识本身不参与编辑；能力位、设备作用域、备注两种模式共用。
 */
function applyFormMode() {
  const editing = editingIdentity !== null
  $('formTitle').textContent = editing ? '编辑身份' : '登记身份'
  $('btnCreate').textContent = editing ? '保存修改' : '登记'
  $('btnCancelEdit').hidden = !editing
  $('kindField').hidden = editing
  $('editingIdentityRow').hidden = !editing
  $('webFields').hidden = editing || currentKind() !== 'web'
  $('openidFields').hidden = editing || currentKind() !== 'openid'
}

/** 把一行身份的值填入表单（编辑模式的控件内容）。 */
function fillForm(item) {
  $('capBox').innerHTML = capabilityChecksHtml(item.capabilities || [])
  $('formScope').innerHTML = scopeEditorHtml(store.devices, item.device_scope || [])
  $('inLabel').value = item.label || ''
  $('editingIdentityValue').textContent = item.identity
  $('editingKindValue').textContent = `来源：${KIND_LABEL[item.kind] || item.kind}`
}

/** 清空表单回登记模式的内容：能力位全不选、作用域全部设备、各输入框置空。 */
function clearForm() {
  $('capBox').innerHTML = capabilityChecksHtml([])
  $('formScope').innerHTML = scopeEditorHtml(store.devices, ['*'])
  $('inLabel').value = ''
  $('inOpenid').value = ''
  $('inCustomIdentity').value = ''
}

/**
 * 进入编辑：把该行值填入下方表单并滚动过去。
 *
 * 表格不重排：重渲染只给该行换一个背景色类，内容不变则列宽行高都不变——
 * 这正是编辑不放进行内的原因。
 */
function enterEdit(item) {
  editingIdentity = item.identity
  editingKind = item.kind
  applyFormMode()
  fillForm(item)
  render(identities)
  report('identityResult', '', 'info')
  $('identityFormPanel').scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/** 退出编辑：表单回到登记模式并清空，行选中态随之撤掉。 */
function exitEdit() {
  editingIdentity = null
  editingKind = 'web'
  applyFormMode()
  clearForm()
  if (identities.length > 0) render(identities)
}

/**
 * 提交表单：编辑模式走 PUT 幂等更新，登记模式走 POST 签发。
 *
 * 能力位与作用域的校验两种模式一致：能力位至少一个；指定设备作用域至少一台。
 */
async function submitIdentityForm() {
  const capabilitiesSelected = readCapabilityChecks($('capBox'))
  if (capabilitiesSelected.length === 0) {
    report('identityResult', '失败 · 至少选择一个能力位', 'err')
    return
  }
  const scope = readScopeEditor($('formScope'))
  if (scope === null) {
    report('identityResult', '失败 · 设备作用域至少选择一台设备，或选择全部设备', 'err')
    return
  }
  const label = $('inLabel').value.trim()

  if (editingIdentity !== null) {
    report('identityResult', '正在保存…', 'info')
    try {
      await api.updateIdentity(editingIdentity, {
        kind: editingKind,
        capabilities: capabilitiesSelected,
        device_scope: scope,
        label,
      })
      toast('已保存，自下一次请求起生效')
      exitEdit()
      await reload()
    } catch (error) {
      report('identityResult', problemText(error), 'err')
    }
    return
  }

  const kind = currentKind()
  const payload = { kind, capabilities: capabilitiesSelected, device_scope: scope, label }
  if (kind === 'openid') {
    const openid = $('inOpenid').value.trim()
    if (!openid) {
      report('identityResult', '失败 · 请填写该用户的 openid', 'err')
      return
    }
    payload.openid = openid
  } else {
    const custom = $('inCustomIdentity').value.trim()
    if (custom) payload.identity = custom
  }

  report('identityResult', '正在登记…', 'info')
  try {
    const created = await api.createIdentity(payload)
    if (kind === 'web' && !$('inCustomIdentity').value.trim()) {
      // 服务端生成的标识明文只在本次响应出现：确认已保存才收起
      showIssuedSecret(created.identity)
    } else {
      report('identityResult', `已登记：${created.identity}`, 'ok')
    }
    $('inOpenid').value = ''
    $('inCustomIdentity').value = ''
    await reload()
  } catch (error) {
    report('identityResult', problemText(error), 'err')
  }
}

/**
 * 签发结果的明文展示区：一键复制 + 显式确认已保存后才收起。
 *
 * 明文不会再次出现，收起必须是显式动作，不靠刷新或点击别处误触。
 */
function showIssuedSecret(identity) {
  $('issuedSecret').hidden = false
  $('issuedValue').value = identity
  report('identityResult', '身份标识已签发，仅显示一次，请立即保存', 'ok')
}

async function copyIssued() {
  const input = $('issuedValue')
  try {
    await navigator.clipboard.writeText(input.value)
    toast('已复制')
  } catch {
    input.select()
    toast('已选中，按 Ctrl+C 复制')
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
          <span class="panel-note" id="identityCount"></span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnReload">刷新</button>
          </div>
        </div>
        <div class="panel-body flush" id="identityTable"></div>
      </section>

      <section class="panel" id="identityFormPanel">
        <div class="panel-head">
          <span class="panel-title" id="formTitle">登记身份</span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnCancelEdit" hidden>取消编辑</button>
          </div>
        </div>
        <div class="panel-body">
          <div class="field" id="kindField">
            <label>来源类型</label>
            <div class="btn-row">
              <label class="field-row">
                <input type="radio" name="identityKind" id="inKindWeb" value="web" checked>
                <span>网页用户（标识可自定义或由服务端生成）</span>
              </label>
              <label class="field-row">
                <input type="radio" name="identityKind" id="inKindOpenid" value="openid">
                <span>小程序用户（登记 openid）</span>
              </label>
            </div>
          </div>
          <div class="field" id="editingIdentityRow" hidden>
            <label>身份标识（不可修改）</label>
            <div class="field-row">
              <code class="mono" id="editingIdentityValue"></code>
              <span class="muted" id="editingKindValue"></span>
            </div>
          </div>
          <div class="field" id="webFields">
            <label for="inCustomIdentity">身份标识（留空则由服务端生成；自定义须为 8～64 个字符，且不含空格）</label>
            <input id="inCustomIdentity" type="text" class="mono" spellcheck="false">
          </div>
          <div class="field" id="openidFields" hidden>
            <label for="inOpenid">微信 openid（小程序转发请求时携带的用户标识）</label>
            <input id="inOpenid" type="text" class="mono" spellcheck="false">
          </div>
          <div class="field">
            <label>能力位</label>
            <div id="capBox"></div>
          </div>
          <div class="field">
            <label>设备作用域</label>
            <div id="formScope"></div>
          </div>
          <div class="field">
            <label for="inLabel">备注（用途）</label>
            <input id="inLabel" type="text">
          </div>
          <div class="btn-row">
            <button type="button" class="btn btn-sm" id="btnCreate">登记</button>
          </div>
          <div class="field" id="issuedSecret" hidden>
            <label for="issuedValue">新签发的身份标识（仅显示一次，确认保存后不再显示）</label>
            <div class="field-row">
              <input id="issuedValue" class="mono" type="text" readonly>
              <button type="button" class="btn btn-ghost btn-sm" id="btnCopyIssued">复制</button>
            </div>
            <div class="btn-row">
              <button type="button" class="btn btn-sm" id="btnIssuedSaved">确认已保存</button>
            </div>
          </div>
          <div class="result" id="identityResult"></div>
        </div>
      </section>`

    dispose = disposer()
    dispose.add(subscribe('view', (id) => { if (id !== 'identities') dispose.dispose() }))
    dispose.add(listen(root.querySelector('#btnReload'), 'click', reload))
    dispose.add(listen($('btnCreate'), 'click', submitIdentityForm))
    dispose.add(listen($('inKindWeb'), 'change', applyFormMode))
    dispose.add(listen($('inKindOpenid'), 'change', applyFormMode))
    dispose.add(listen($('btnCancelEdit'), 'click', exitEdit))
    dispose.add(listen($('btnCopyIssued'), 'click', copyIssued))
    dispose.add(
      listen($('btnIssuedSaved'), 'click', () => {
        $('issuedSecret').hidden = true
        $('issuedValue').value = ''
        toast('已确认保存')
      }),
    )

    // 作用域开关联动（登记与编辑共用一条委托），实现收在 core/device-scope.js
    dispose.add(bindScopeToggle(root))

    // 名单事件委托：编辑与撤销。编辑只把值搬进表单，表格本身不动
    dispose.add(
      listen($('identityTable'), 'click', async (event) => {
        const button = event.target.closest('button[data-act]')
        if (!button) return
        const identity = button.closest('tr').dataset.id
        if (button.dataset.act === 'edit') {
          const item = identities.find((row) => row.identity === identity)
          if (item) enterEdit(item)
          return
        }
        if (button.dataset.act === 'revoke') {
          if (!window.confirm(`撤销 ${identity}？该身份标识的全部能力位将立即失效。`)) return
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

    exitEdit()
    reload()
  },

  unmount() {
    dispose.dispose()
  },
}
