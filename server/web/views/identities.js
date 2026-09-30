/**
 * 身份：谁可以操作这个系统。
 *
 * 与「设备」里的 `trusted` 不是一回事，尽管都叫"授权"：
 * - 这里的**身份**是服务端的调用者（小程序用户 / 网页端），决定谁能下发会话操作
 * - 设备上的 `trusted` 是那台机器在服务端的**档案位**，决定是否给它下发命令
 *
 * 一个身份持有若干**能力位**（capabilities），服务端按它拦截操作类接口。
 * 查看类接口不需要身份，所以这里不发令牌也不影响别人看状态。
 */
import { api } from '../core/api.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { toast } from '../core/toast.js'

/** 能力位的取值由服务端定义（`access/identity.py`），这里只负责展示与勾选。 */
const CAPABILITIES = [
  { key: 'ops', label: '会话操作', hint: '分配、结算' },
  { key: 'photo', label: '拍照', hint: '抓取现场画面' },
]

/** 能力位的中文名；没见过的取值原样显示，让人看得见是哪一个。 */
const CAPABILITY_LABEL = Object.fromEntries(CAPABILITIES.map((item) => [item.key, item.label]))

/** 标识来源的中文名。取值见服务端 `access/identity.py` 的 `IdentityKind`。 */
const KIND_LABEL = {
  openid: '小程序用户',
  web: '网页用户',
}

function capabilityBadges(values) {
  if (!values || values.length === 0) return '<span class="muted">无</span>'
  return values.map((value) => `<span class="badge badge-blue">${esc(CAPABILITY_LABEL[value] || value)}</span>`).join(' ')
}

function report(id, text, kind) {
  const box = $(id)
  box.className = `result ${kind}`
  box.textContent = text
}

function render(list) {
  if (list.length === 0) {
    $('identityTable').innerHTML = '<div class="empty">暂无身份记录</div>'
    return
  }
  const rows = list
    .map(
      (item) => `
      <tr data-id="${esc(item.identity)}">
        <td><code>${esc(item.identity)}</code></td>
        <td>${esc(KIND_LABEL[item.kind] || item.kind)}</td>
        <td>${capabilityBadges(item.capabilities)}</td>
        <td>${esc(item.label || PLACEHOLDER)}</td>
        <td><button type="button" class="btn btn-danger btn-sm" data-act="revoke">撤销</button></td>
      </tr>`,
    )
    .join('')
  $('identityTable').innerHTML = `<table class="table"><thead><tr>
      <th>标识</th><th>来源</th><th>能力位</th><th>备注</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

async function reload() {
  try {
    render(await api.identities())
  } catch (error) {
    report('identityResult', error.message, 'err')
  }
}

async function create() {
  const label = $('inLabel').value.trim()
  const capabilities = [...$('capBox').querySelectorAll('input:checked')].map((input) => input.value)
  if (capabilities.length === 0) {
    report('identityResult', '至少选一个能力位', 'err')
    return
  }
  report('identityResult', '正在登记…', 'info')
  try {
    // kind=web：由服务端签发标识，明文只在本次响应返回，所以必须当场展示
    const created = await api.createIdentity({ kind: 'web', capabilities, label, openid: '' })
    report('identityResult', `已签发：${created.identity}（请立即记录，明文不会再次显示）`, 'ok')
    await reload()
  } catch (error) {
    report('identityResult', error.message, 'err')
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
        <div class="panel-head"><span class="panel-title">登记网页标识</span></div>
        <div class="panel-body">
          <div class="field">
            <label for="inLabel">备注（给谁用的）</label>
            <input id="inLabel" type="text" placeholder="例：运维值班">
          </div>
          <div class="field">
            <label>能力位</label>
            <div id="capBox">
              ${CAPABILITIES.map(
                (cap) => `<label class="field-row" style="margin-bottom:4px">
                  <input type="checkbox" value="${esc(cap.key)}">
                  <span>${esc(cap.label)}<span class="muted"> · ${esc(cap.hint)}</span></span>
                </label>`,
              ).join('')}
            </div>
          </div>
          <div class="btn-row">
            <button type="button" class="btn btn-sm" id="btnCreate">登记</button>
          </div>
          <div class="result" id="identityResult"></div>
        </div>
      </section>`

    dispose = disposer()
    dispose.add(subscribe('view', (id) => { if (id !== 'identities') dispose.dispose() }))
    dispose.add(listen($('btnReload'), 'click', reload))
    dispose.add(listen($('btnCreate'), 'click', create))
    dispose.add(
      listen($('identityTable'), 'click', async (event) => {
        const button = event.target.closest('button[data-act]')
        if (!button) return
        const identity = button.closest('tr').dataset.id
        if (!window.confirm(`撤销 ${identity}？该标识立即失去全部能力。`)) return
        try {
          await api.deleteIdentity(identity)
          toast('已撤销')
          await reload()
        } catch (error) {
          report('identityResult', error.message, 'err')
        }
      }),
    )

    reload()
  },

  unmount() {
    dispose.dispose()
  },
}
