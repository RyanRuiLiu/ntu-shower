/**
 * 用户：谁可以用这个系统、有什么权限、对哪些设备。
 *
 * ## 模型（身份 × 密钥）
 *
 * - **用户**（users）回答"是谁"：username 唯一，name 是显示名；有口令即可登录
 *   管理台，没有即纯密钥调用方（GitHub 的 machine user 同型，不设类型字段）。
 * - **密钥**（api_keys）回答"能做什么"：权限（scopes）与设备作用域都附在每枚
 *   密钥上，不附在用户上（GitHub fine-grained PAT 同型）。权限清单来自
 *   `GET /users` 的域读模型（服务端字典是唯一来源），不硬编码第二份。
 * - 密钥默认长期有效；轮换是显式动作——发一枚新的、切过去、吊销旧的，多枚共存
 *   天然提供重叠窗口。列表里的"密钥"列是摘要前 12 位，与审计记录对账用。
 *
 * ## 热更新
 *
 * 权限、作用域与禁用的改动即时生效：服务端每个请求实时查库，无缓存无重启。
 *
 * ## 编辑
 *
 * 编辑不在表格行内进行：行内控件会把该行撑高、迫使全表列宽重排。表格只作展示，
 * 点「编辑」把该行值填入下方表单（登记/编辑双模式）并滚动过去；保存走 PUT。
 */
import { api } from '../core/api.js'
import { copyText } from '../core/clipboard.js'
import { fmtFullDateTime } from '../core/clock.js'
import { codeCell } from '../core/cells.js'
import { problemText, report } from '../core/outcome.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { bindScopeToggle, readScopeEditor, scopeEditorHtml } from '../core/device-scope.js'
import { deviceName, refreshDevices, store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

let scopes = []
let users = []
/** 编辑态：正在编辑的用户名；null 即登记模式。 */
let editingUser = null
/** 密钥面板当前展开的用户；null 即收起。 */
let keysUser = null
let keys = []
/** 密钥编辑态：正在编辑的密钥 id；null 即签发模式。 */
let editingKeyId = null

function scopeText(scope) {
  if (!scope || scope.length === 0) return PLACEHOLDER
  if (scope.includes('*')) return '全部设备'
  return scope.map(deviceName).join('、')
}

function scopeBadges(values) {
  if (!values || values.length === 0) return '<span class="muted">无</span>'
  const byScope = Object.fromEntries(scopes.map((item) => [item.scope, item.label]))
  return values.map((value) => `<span class="badge badge-blue">${esc(byScope[value] || value)}</span>`).join(' ')
}

/** 容器内勾选的权限集合（勾选框不带 value，键在 data-scope 上）。 */
function readScopeChecks(container) {
  return [...container.querySelectorAll('input[data-scope]:checked')].map((input) => input.dataset.scope)
}

/** 权限勾选组（登记与编辑密钥共用）。勾「全部权限」即禁用并清空具体项，取消后恢复——GitHub 同款互斥。 */
function scopeChecksHtml(selected) {
  const checks = scopes
    .map((item) => {
      const on = selected.includes(item.scope)
      const wildcard = item.scope === '*' ? ' data-wildcard="1"' : ' data-specific="1"'
      return `<label class="field-row">
          <input type="checkbox" data-scope="${esc(item.scope)}"${wildcard} ${on ? 'checked' : ''}>
          <span>${esc(item.label)}</span>
        </label>`
    })
    .join('')
  return `<div class="btn-row">${checks}</div>`
}

/** 通配与具体权限的互斥绑定：渲染勾选组之后调一次。 */
function bindWildcardToggle(container) {
  const wildcard = container.querySelector('input[data-wildcard]')
  const specifics = [...container.querySelectorAll('input[data-specific]')]
  const apply = () => {
    specifics.forEach((input) => {
      input.disabled = wildcard.checked
      if (wildcard.checked) input.checked = false
    })
  }
  wildcard.addEventListener('change', apply)
  apply()
}

function render() {
  $('userCount').textContent = users.length > 0 ? `共 ${users.length} 条` : ''
  if (users.length === 0) {
    $('userTable').innerHTML = '<div class="empty">暂无用户；使用右侧表单登记用户</div>'
  } else {
    const rows = users
      .map((item) => {
        // 正在编辑的行标出选中态：只换背景色，不动结构，列宽与行高保持不变
        const selected = editingUser === item.username ? ' class="is-selected"' : ''
        const state = item.disabled
          ? '<span class="badge badge-red">已禁用</span>'
          : '<span class="badge badge-green">在用</span>'
        return `<tr${selected} data-id="${esc(item.username)}">
        <td>${codeCell(item.username)}</td>
        <td>${esc(item.name || PLACEHOLDER)}</td>
        <td>${state}</td>
        <td>${item.has_password ? '可登录' : '<span class="muted">仅密钥</span>'}</td>
        <td>${fmtFullDateTime(item.last_login_at)}</td>
        <td class="col-actions">
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="edit">编辑</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="keys">密钥</button>
            <button type="button" class="btn btn-danger btn-sm" data-act="delete">删除</button>
          </div>
        </td>
      </tr>`
      })
      .join('')
    $('userTable').innerHTML = `<table class="table"><thead><tr>
      <th>用户名</th><th>显示名</th><th>状态</th><th>登录方式</th><th>最近登录</th><th class="col-actions">操作</th>
    </tr></thead><tbody>${rows}</tbody></table>`
  }

  // 密钥面板：未选用户时给引导文案
  if (keysUser === null) {
    $('keysBody').innerHTML = '<div class="empty">点上方「密钥」查看或签发某用户的密钥</div>'
    $('keysForm').hidden = true
  } else {
    $('keysTitle').textContent = `密钥：${keysUser}`
    $('keysForm').hidden = false
    const rows = keys.map((key) => {
      const state = key.revoked_at
        ? '<span class="badge badge-red">已吊销</span>'
        : '<span class="badge badge-green">在用</span>'
      return `<tr>
        <td>${esc(key.name || PLACEHOLDER)}</td>
        <td>${scopeBadges(key.scopes)}</td>
        <td title="${esc((key.device_scope || []).join(', '))}">${esc(scopeText(key.device_scope))}</td>
        <td>${codeCell(key.token_hint)}</td>
        <td>${fmtFullDateTime(key.last_used_at)}</td>
        <td>${state}</td>
        <td class="col-actions">
          ${
            key.revoked_at
              ? ''
              : `<div class="btn-row">
                  <button type="button" class="btn btn-ghost btn-sm" data-act="edit-key" data-key="${key.id}">编辑</button>
                  <button type="button" class="btn btn-danger btn-sm" data-act="revoke" data-key="${key.id}">吊销</button>
                </div>`
          }
        </td>
      </tr>`
    })
    $('keysBody').innerHTML = `<table class="table"><thead><tr>
      <th>名称</th><th>权限</th><th>设备作用域</th><th>密钥（摘要前 12 位）</th><th>最后使用</th><th>状态</th><th class="col-actions">操作</th>
    </tr></thead><tbody>${rows.join('')}</tbody></table>`
  }
}

async function reload() {
  try {
    // 设备清单经 store 的 setter 拉取：作用域编辑器与 deviceName 都读 store.devices
    const [bundle] = await Promise.all([api.users(), refreshDevices()])
    scopes = bundle.scopes
    users = bundle.users
    if (keysUser !== null && !users.some((item) => item.username === keysUser)) keysUser = null
    if (keysUser !== null) keys = (await api.userKeys(keysUser)).keys
    render()
    if (editingUser !== null) {
      // 编辑中刷新：以清单现值重填表单；该用户已不存在则回到登记模式
      const item = users.find((row) => row.username === editingUser)
      if (item) fillForm(item)
      else exitEdit()
    } else {
      clearForm()
    }
  } catch (error) {
    report('userResult', problemText(error), 'err')
  }
}

/** 表单双模式切换：登记（POST）与编辑（PUT）共用同一组控件。 */
function applyFormMode() {
  const editing = editingUser !== null
  $('formTitle').textContent = editing ? '编辑用户' : '登记用户'
  $('btnCreate').textContent = editing ? '保存修改' : '登记'
  $('btnCancelEdit').hidden = !editing
  $('usernameField').hidden = editing
  $('editingUserRow').hidden = !editing
  $('disabledField').hidden = !editing
}

/** 把一行用户的值填入表单（编辑模式的控件内容）。 */
function fillForm(item) {
  $('inName').value = item.name || ''
  $('inPassword').value = ''
  $('inDisabled').checked = item.disabled
  $('editingUserValue').textContent = item.username
}

/** 清空表单回登记模式的内容。 */
function clearForm() {
  $('inName').value = ''
  $('inPassword').value = ''
  $('inUsername').value = ''
  $('inDisabled').checked = false
}

/**
 * 进入编辑：把该行值填入下方表单并滚动过去。
 *
 * 表格不重排：重渲染只给该行换一个背景色类，内容不变则列宽行高都不变。
 */
function enterEdit(item) {
  editingUser = item.username
  applyFormMode()
  fillForm(item)
  render()
  report('userResult', '', 'info')
  $('userFormPanel').scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/** 退出编辑：表单回到登记模式并清空，行选中态随之撤掉。 */
function exitEdit() {
  editingUser = null
  applyFormMode()
  clearForm()
  render()
}

async function submitUserForm() {
  const password = $('inPassword').value
  if (editingUser !== null) {
    report('userResult', '正在保存…', 'info')
    try {
      const payload = { name: $('inName').value.trim(), disabled: $('inDisabled').checked }
      if (password) payload.password = password
      await api.updateUser(editingUser, payload)
      report('userResult', '已保存', 'ok')
      exitEdit()
      await reload()
    } catch (error) {
      report('userResult', problemText(error), 'err')
    }
    return
  }
  const username = $('inUsername').value.trim()
  if (!username) {
    report('userResult', '失败 · 请填写用户名', 'err')
    return
  }
  report('userResult', '正在登记…', 'info')
  try {
    await api.createUser({ username, name: $('inName').value.trim(), password: password || undefined })
    report('userResult', '已登记', 'ok')
    clearForm()
    await reload()
  } catch (error) {
    report('userResult', problemText(error), 'err')
  }
}

/**
 * 签发（POST）与编辑（PUT）密钥：权限与设备作用域都附在这枚钥上。
 *
 * 签发的明文只在本次响应出现一次；编辑不碰令牌，保存即生效。权限至少一个；
 * 勾选「全部权限」时二次确认并规范化为 ["*"]。成功后刷新该用户的密钥列表。
 */
async function submitKeyForm() {
  if (keysUser === null) return
  const checked = readScopeChecks($('keyScopeBox'))
  if (checked.length === 0) {
    report('keyResult', '失败 · 至少选择一个权限', 'err')
    return
  }
  // 通配归一：勾了 * 就只存 *，混着具体权限是两份语义
  const selected = checked.includes('*') ? ['*'] : checked
  if (selected[0] === '*' && !window.confirm('签发「全部权限」密钥？它将包含现在与未来新增的一切权限，只应发给自己的脚本与内部工具。')) {
    return
  }
  const deviceScope = readScopeEditor($('keyFormScope'))
  if (deviceScope === null) {
    report('keyResult', '失败 · 设备作用域至少选择一台设备，或选择全部设备', 'err')
    return
  }
  const payload = { name: $('inKeyName').value.trim(), scopes: selected, device_scope: deviceScope }
  if (editingKeyId !== null) {
    report('keyResult', '正在保存…', 'info')
    try {
      await api.updateKey(keysUser, editingKeyId, payload)
      report('keyResult', '已保存', 'ok')
      exitKeyEdit()
      await reload()
    } catch (error) {
      report('keyResult', problemText(error), 'err')
    }
    return
  }
  report('keyResult', '正在签发…', 'info')
  try {
    const issued = await api.createKey(keysUser, payload)
    showIssuedSecret(issued.token, '已签发：明文仅显示一次，请立即保存')
    $('inKeyName').value = ''
    await reload()
  } catch (error) {
    report('keyResult', problemText(error), 'err')
  }
}

/**
 * 签发结果的凭据展示区：一键复制 + 显式确认已保存后才收起。
 *
 * 凭据不会再次出现，收起必须是显式动作，不靠刷新或点击别处误触。
 */
function showIssuedSecret(token, message) {
  $('issuedSecret').hidden = false
  $('issuedValue').value = token
  report('keyResult', message, 'ok')
}

function copyIssued() {
  copyText($('issuedValue').value)
}

/** 密钥表单双模式：签发（POST）与编辑（PUT，令牌不变）共用同一组控件。 */
function applyKeyFormMode() {
  const editing = editingKeyId !== null
  $('btnIssueKey').textContent = editing ? '保存修改' : '签发密钥'
  $('btnCancelKeyEdit').hidden = !editing
  $('editingKeyRow').hidden = !editing
}

/** 把一枚密钥的值填入表单（编辑模式的控件内容）。 */
function fillKeyForm(key) {
  $('inKeyName').value = key.name || ''
  $('keyScopeBox').innerHTML = scopeChecksHtml(key.scopes || [])
  bindWildcardToggle($('keyScopeBox'))
  $('keyFormScope').innerHTML = scopeEditorHtml(store.devices, key.device_scope || [])
  $('editingKeyValue').textContent = `#${key.id}（${key.token_hint}）`
}

/** 密钥表单回签发模式并清空。 */
function clearKeyForm() {
  $('inKeyName').value = ''
  $('keyScopeBox').innerHTML = scopeChecksHtml([])
  bindWildcardToggle($('keyScopeBox'))
  $('keyFormScope').innerHTML = scopeEditorHtml(store.devices, ['*'])
}

function exitKeyEdit() {
  editingKeyId = null
  applyKeyFormMode()
  clearKeyForm()
}

let dispose = disposer()

export const usersView = {
  id: 'users',
  title: '用户',

  mount(root) {
    root.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">用户名单</span>
          <span class="panel-note" id="userCount"></span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnReload">刷新</button>
          </div>
        </div>
        <div class="panel-body flush" id="userTable"></div>
      </section>

      <section class="panel" id="userFormPanel">
        <div class="panel-head">
          <span class="panel-title" id="formTitle">登记用户</span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnCancelEdit" hidden>取消编辑</button>
          </div>
        </div>
        <div class="panel-body">
          <div class="field" id="usernameField">
            <label for="inUsername">用户名（唯一；外部系统登记一个专用名，管理员登台用另取）</label>
            <input id="inUsername" type="text" class="mono" spellcheck="false">
          </div>
          <div class="field" id="editingUserRow" hidden>
            <span class="field-label">用户名（不可修改）</span>
            <code class="mono" id="editingUserValue"></code>
          </div>
          <div class="field">
            <label for="inName">显示名（用途或人名）</label>
            <input id="inName" type="text">
          </div>
          <div class="field">
            <label for="inPassword">口令（留空不动；设置后该用户即可登录管理台）</label>
            <input id="inPassword" type="password" autocomplete="new-password">
          </div>
          <div class="field" id="disabledField" hidden>
            <label class="field-row">
              <input id="inDisabled" type="checkbox">
              <span>禁用该用户（其全部密钥与会话立即失效）</span>
            </label>
          </div>
          <div class="btn-row">
            <button type="button" class="btn btn-sm" id="btnCreate">登记</button>
          </div>
          <div class="result" id="userResult"></div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <span class="panel-title" id="keysTitle">密钥</span>
        </div>
        <div class="panel-body">
          <div id="keysBody"></div>
          <div id="keysForm" hidden>
            <div class="field" id="editingKeyRow" hidden>
              <span class="field-label">正在编辑（令牌不变，保存即生效）</span>
              <code class="mono" id="editingKeyValue"></code>
            </div>
            <div class="field">
              <label for="inKeyName">密钥名称（用途）</label>
              <input id="inKeyName" type="text">
            </div>
            <div class="field">
              <span class="field-label" id="keyScopeLabel">权限</span>
              <div id="keyScopeBox" role="group" aria-labelledby="keyScopeLabel"></div>
            </div>
            <div class="field">
              <span class="field-label" id="keyFormScopeLabel">设备作用域</span>
              <div id="keyFormScope" role="group" aria-labelledby="keyFormScopeLabel"></div>
            </div>
            <div class="btn-row">
              <button type="button" class="btn btn-sm" id="btnIssueKey">签发密钥</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnCancelKeyEdit" hidden>取消编辑</button>
            </div>
            <div class="field" id="issuedSecret" hidden>
              <label for="issuedValue">密钥（Authorization: Bearer 用；仅显示一次，确认保存后不再显示）</label>
              <div class="field-row">
                <input id="issuedValue" class="mono" type="text" readonly>
                <button type="button" class="btn btn-ghost btn-sm" id="btnCopyIssued">复制</button>
              </div>
              <div class="btn-row">
                <button type="button" class="btn btn-sm" id="btnIssuedSaved">确认已保存</button>
              </div>
            </div>
            <div class="result" id="keyResult"></div>
          </div>
        </div>
      </section>`

    dispose = disposer()
    dispose.add(subscribe('view', (id) => { if (id !== 'users') dispose.dispose() }))
    dispose.add(listen(root.querySelector('#btnReload'), 'click', reload))
    dispose.add(listen($('btnCreate'), 'click', submitUserForm))
    dispose.add(listen($('btnCancelEdit'), 'click', exitEdit))
    dispose.add(listen($('btnIssueKey'), 'click', submitKeyForm))
    dispose.add(listen($('btnCancelKeyEdit'), 'click', () => { exitKeyEdit(); render() }))
    dispose.add(listen($('btnCopyIssued'), 'click', copyIssued))
    dispose.add(
      listen($('btnIssuedSaved'), 'click', () => {
        $('issuedSecret').hidden = true
        $('issuedValue').value = ''
        toast('已确认保存')
      }),
    )

    // 作用域开关联动（密钥表单用；实现收在 core/device-scope.js）
    dispose.add(bindScopeToggle(root))

    // 名单事件委托：编辑、密钥、删除
    dispose.add(
      listen($('userTable'), 'click', async (event) => {
        const button = event.target.closest('button[data-act]')
        if (!button) return
        const username = button.closest('tr').dataset.id
        if (button.dataset.act === 'edit') {
          const item = users.find((row) => row.username === username)
          if (item) enterEdit(item)
          return
        }
        if (button.dataset.act === 'keys') {
          keysUser = username
          keys = (await api.userKeys(username)).keys
          $('keyScopeBox').innerHTML = scopeChecksHtml([])
          bindWildcardToggle($('keyScopeBox'))
          $('keyFormScope').innerHTML = scopeEditorHtml(store.devices, ['*'])
          render()
          return
        }
        if (button.dataset.act === 'delete') {
          if (!window.confirm(`删除 ${username}？其全部密钥将一并删除并立即失效。`)) return
          try {
            await api.deleteUser(username)
            toast('已删除')
            await reload()
          } catch (error) {
            report('userResult', problemText(error), 'err')
          }
        }
      }),
    )

    // 密钥表事件委托：编辑与吊销单枚
    dispose.add(
      listen($('keysBody'), 'click', async (event) => {
        const button = event.target.closest('button[data-act]')
        if (!button) return
        if (button.dataset.act === 'edit-key') {
          const key = keys.find((row) => row.id === Number(button.dataset.key))
          if (!key) return
          editingKeyId = key.id
          applyKeyFormMode()
          fillKeyForm(key)
          report('keyResult', '', 'info')
          return
        }
        if (button.dataset.act !== 'revoke') return
        if (!window.confirm('吊销这枚密钥？该枚下一次请求即失效，其他密钥不受影响。')) return
        try {
          await api.revokeKey(keysUser, button.dataset.key)
          toast('已吊销')
          if (editingKeyId === Number(button.dataset.key)) exitKeyEdit()
          keys = (await api.userKeys(keysUser)).keys
          render()
        } catch (error) {
          report('keyResult', problemText(error), 'err')
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
