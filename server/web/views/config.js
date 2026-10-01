/**
 * 配置：全局默认与设备覆盖，卡片式分模块。
 *
 * 表单**由服务端的登记表生成**（`GET /ops/config/spec`），不在这里再抄一份键名、
 * 类型与取值范围——抄一份就是多一个会漂移的事实源，加一个配置项要改两处，而漏改
 * 的那一处不报错，只是那个键从此改不动。
 *
 * ## 分组
 *
 * 登记表带 `group`（服务端 spec.GROUPS，单一来源），前端按它把键分成卡片；
 * 卡片顺序即 `groups` 清单的顺序。同一张卡片里的键是同一件事的参数，找起来
 * 不用在 35 个键的扁平长列里滚。
 *
 * ## 两栏语义
 *
 * 取值是三级合并：**默认 ← 全局覆盖 ← 设备覆盖**（见 `config/store.py`）。
 * 页面分左右两栏正是后面两级：左边改全局，右边改当前设备。
 *
 * ## 控件形态按 kind
 *
 * bool → 勾选框；int → 数字输入（带上下限与单位）；text → 文本框；
 * text_map（如自动结算名单）→ "键=值" 行编辑器：一行一个映射，空行忽略，
 * 保存时解析回对象——值里含 "=" 的场景用第一个 "=" 分隔。
 */
import { api } from '../core/api.js'
import { problemText, report } from '../core/outcome.js'
import { $, disposer, esc, listen } from '../core/dom.js'
import { store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

let groups = []
let specs = []

const SCOPE_LABEL = { device: '设备', server: '服务端' }

function controlHtml(spec, value, id) {
  const described = `<div class="cfg-desc">${esc(spec.description)}</div>`
  if (spec.kind === 'bool') {
    const checked = value ? 'checked' : ''
    return `<label class="field-row" for="${esc(id)}">
        <input id="${esc(id)}" type="checkbox" data-key="${esc(spec.key)}" data-kind="bool" ${checked}>
        <span>${esc(spec.title)}</span>
      </label>${described}`
  }
  if (spec.kind === 'text_map') {
    // 对象 → "键=值" 多行文本；一行一个映射，展示顺序即提交后的对象键序
    const lines = Object.entries(value || {})
      .map(([mapKey, mapValue]) => `${mapKey}=${mapValue}`)
      .join('\n')
    return `<div class="field">
        <label for="${esc(id)}">${esc(spec.title)}<span class="muted"> · 一行一个，格式：学号=密码</span></label>
        <textarea id="${esc(id)}" data-key="${esc(spec.key)}" data-kind="text_map" rows="4"
                  spellcheck="false" placeholder="2530110052=000000">${esc(lines)}</textarea>
      </div>${described}`
  }
  const min = spec.minimum === null ? '' : `min="${spec.minimum}"`
  const max = spec.maximum === null ? '' : `max="${spec.maximum}"`
  return `<div class="field">
      <label for="${esc(id)}">${esc(spec.title)}${spec.unit ? `（${esc(spec.unit)}）` : ''}
        <span class="cfg-scope">${esc(SCOPE_LABEL[spec.scope] || spec.scope)}</span></label>
      <input id="${esc(id)}" type="number" data-key="${esc(spec.key)}" data-kind="int"
             value="${esc(value)}" ${min} ${max}>
    </div>${described}`
}

function renderForm(container, values, prefix) {
  const used = new Set()
  const cards = groups
    .map(({ key: groupKey, title }) => {
      const groupSpecs = specs.filter((spec) => spec.group === groupKey)
      if (groupSpecs.length === 0) return ''
      groupSpecs.forEach((spec) => used.add(spec.key))
      return `<section class="panel cfg-card">
          <div class="panel-head"><span class="panel-title">${esc(title)}</span></div>
          <div class="panel-body">${groupSpecs
            .map((spec) => controlHtml(spec, values[spec.key], `${prefix}-${spec.key}`))
            .join('<div class="cfg-sep"></div>')}</div>
        </section>`
    })
    .join('')
  // 登记表里有、但分组清单没覆盖的键（漏登记 group）不能静默消失——归进"其他"
  const orphans = specs.filter((spec) => !used.has(spec.key))
  if (orphans.length > 0) {
    throw new Error(`配置键缺少分组：${orphans.map((spec) => spec.key).join('、')}`)
  }
  container.innerHTML = cards
}

function parseTextMap(text) {
  const result = {}
  for (const line of text.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const eq = trimmed.indexOf('=')
    if (eq <= 0) throw new Error(`格式不对：${trimmed}（应为 学号=密码）`)
    result[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim()
  }
  return result
}

function readForm(container) {
  const config = {}
  for (const input of container.querySelectorAll('[data-key]')) {
    const key = input.dataset.key
    if (input.dataset.kind === 'bool') {
      config[key] = input.checked
    } else if (input.dataset.kind === 'text_map') {
      config[key] = parseTextMap(input.value)
    } else {
      const value = Number(input.value)
      if (Number.isFinite(value)) config[key] = value
    }
  }
  return config
}

async function loadGlobal() {
  try {
    const view = await api.globalConfig()
    renderForm($('globalForm'), view.config, 'g')
    $('globalNote').textContent = `修订 ${view.revision}`
  } catch (error) {
    report('globalResult', problemText(error), 'err')
  }
}

async function loadDevice() {
  if (!store.deviceId) {
    $('deviceForm').innerHTML = '<div class="empty">请先在顶栏选择设备</div>'
    $('deviceNote').textContent = ''
    return
  }
  try {
    const view = await api.deviceConfig(store.deviceId)
    renderForm($('deviceForm'), view.config, 'd')
    $('deviceNote').textContent = view.overrides
      ? `修订 ${view.revision} · 已覆盖 ${Object.keys(view.overrides).length} 项`
      : `修订 ${view.revision} · 跟随全局`
  } catch (error) {
    report('deviceResult', problemText(error), 'err')
  }
}

async function saveGlobal() {
  report('globalResult', '正在保存…', 'info')
  try {
    const result = await api.saveGlobalConfig(readForm($('globalForm')))
    const notified = result.notified || []
    const applied = notified.filter((item) => item.applied).length
    report('globalResult', `已保存（修订 ${result.revision}），推送 ${notified.length} 台、生效 ${applied} 台`, 'ok')
    await loadDevice()
  } catch (error) {
    report('globalResult', problemText(error), 'err')
  }
}

async function saveDevice() {
  if (!store.deviceId) return
  report('deviceResult', '正在保存…', 'info')
  try {
    const result = await api.saveDeviceConfig(store.deviceId, readForm($('deviceForm')))
    report('deviceResult', `已保存（修订 ${result.revision}）${result.applied ? '，设备已生效' : `，${result.reason || '未生效'}`}`, 'ok')
    await loadDevice()
  } catch (error) {
    report('deviceResult', problemText(error), 'err')
  }
}

async function clearDevice() {
  if (!store.deviceId) return
  if (!window.confirm('删除这台设备的配置覆盖？它将回落到全局值。')) return
  try {
    await api.clearDeviceConfig(store.deviceId)
    toast('已回落为跟随全局')
    await loadDevice()
  } catch (error) {
    report('deviceResult', problemText(error), 'err')
  }
}

let dispose = disposer()

export const configView = {
  id: 'config',
  title: '配置',

  mount(root) {
    root.innerHTML = `
      <div class="grid grid-2">
        <section class="panel">
          <div class="panel-head">
            <span class="panel-title">全局默认</span>
            <span class="panel-note" id="globalNote"></span>
            <div class="panel-tools">
              <button type="button" class="btn btn-sm" id="btnSaveGlobal">保存并推送</button>
            </div>
          </div>
          <div class="panel-body">
            <div id="globalForm"><div class="empty">正在加载…</div></div>
            <div class="result" id="globalResult"></div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <span class="panel-title">设备覆盖</span>
            <span class="panel-note" id="deviceNote"></span>
            <div class="panel-tools">
              <button type="button" class="btn btn-ghost btn-sm" id="btnClearDevice">删除覆盖</button>
              <button type="button" class="btn btn-sm" id="btnSaveDevice">保存并推送</button>
            </div>
          </div>
          <div class="panel-body">
            <div id="deviceForm"><div class="empty">正在加载…</div></div>
            <div class="result" id="deviceResult"></div>
          </div>
        </section>
      </div>`

    dispose = disposer()
    dispose.add(subscribe('device', loadDevice))
    dispose.add(subscribe('view', (id) => { if (id !== 'config') dispose.dispose() }))
    dispose.add(listen($('btnSaveGlobal'), 'click', saveGlobal))
    dispose.add(listen($('btnSaveDevice'), 'click', saveDevice))
    dispose.add(listen($('btnClearDevice'), 'click', clearDevice))

    api.configSpec()
      .then((view) => {
        groups = view.groups
        specs = view.specs
        return Promise.all([loadGlobal(), loadDevice()])
      })
      .catch((error) => report('globalResult', problemText(error), 'err'))
  },

  unmount() {
    dispose.dispose()
  },
}
