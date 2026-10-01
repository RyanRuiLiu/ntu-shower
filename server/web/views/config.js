/**
 * 配置：单页单表单，编辑"当前设备的有效配置"，继承与覆盖显式标记。
 *
 * ## 为什么不是双栏（全局一栏、设备一栏）
 *
 * 那种做法把同一批键渲染两遍：35 个键要在两栏里各看一次、改一处对不上一处，
 * 视觉重量翻倍而信息量没变。业界标准（DoorDash / Superset 的设置继承模式）是
 * **一层表单**：表单编辑"这台设备现在实际生效的值"（默认 ← 全局覆盖 ← 设备覆盖
 * 三级合并的结果），每个键用徽标记出它处于哪一级——"继承全局"的键改一下就自动
 * 变成设备覆盖，"已覆盖"的键一键回落到继承值。全局默认的编辑入口单独保留
 * （那是"所有设备的出厂值"，用得少，不值得与设备视图平铺）。
 *
 * ## 分组
 *
 * 登记表带 `group`（服务端 spec.GROUPS，单一来源），前端按它把键分成卡片；
 * 卡片顺序即 `groups` 清单的顺序。
 *
 * ## 控件形态按 kind
 *
 * bool → 勾选框；int → 数字输入（带上下限与单位）；text → 文本框；
 * text_map（如自动结算名单）→ "键=值" 行编辑器。
 */
import { api } from '../core/api.js'
import { problemText, report } from '../core/outcome.js'
import { $, disposer, esc, listen } from '../core/dom.js'
import { store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

let groups = []
let specs = []
/** 设备覆盖项的键集合（其余键处于继承态）。 */
let overriddenKeys = new Set()

const INHERITED_BADGE = '<span class="cfg-inherit">继承</span>'
const OVERRIDE_BADGE = '<span class="cfg-override">覆盖</span>'

function badge(isOverridden) {
  return isOverridden ? OVERRIDE_BADGE : INHERITED_BADGE
}

/** 继承态的键展示的是全局有效值——先拿到它，回落与展示都用。 */
let globalEffective = {}

function controlHtml(spec, value, id, isOverridden) {
  const desc = `<div class="cfg-desc">${esc(spec.description)}</div>`
  const badgeHtml = badge(isOverridden)
  const resetButton = isOverridden
    ? `<button type="button" class="btn btn-ghost btn-sm cfg-reset" data-reset="${esc(spec.key)}" title="删除这条设备覆盖，回落到全局值">回落</button>`
    : ''
  if (spec.kind === 'bool') {
    const checked = value ? 'checked' : ''
    return `<label class="field-row" for="${esc(id)}">
        <input id="${esc(id)}" type="checkbox" data-key="${esc(spec.key)}" data-kind="bool" ${checked}>
        <span>${esc(spec.title)} ${badgeHtml}</span>${resetButton}
      </label>${desc}`
  }
  if (spec.kind === 'text_map') {
    const lines = Object.entries(value || {})
      .map(([mapKey, mapValue]) => `${mapKey}=${mapValue}`)
      .join('\n')
    return `<div class="field">
        <label for="${esc(id)}">${esc(spec.title)} ${badgeHtml}${resetButton}
          <span class="muted"> · 一行一个，格式：学号=密码</span></label>
        <textarea id="${esc(id)}" data-key="${esc(spec.key)}" data-kind="text_map" rows="4"
                  spellcheck="false" placeholder="2530110052=000000">${esc(lines)}</textarea>
      </div>${desc}`
  }
  const min = spec.minimum === null ? '' : `min="${spec.minimum}"`
  const max = spec.maximum === null ? '' : `max="${spec.maximum}"`
  return `<div class="field">
      <label for="${esc(id)}">${esc(spec.title)}${spec.unit ? `（${esc(spec.unit)}）` : ''} ${badgeHtml}${resetButton}</label>
      <input id="${esc(id)}" type="number" data-key="${esc(spec.key)}" data-kind="int"
             value="${esc(value)}" ${min} ${max}>
    </div>${desc}`
}

function renderForm(container, values) {
  const cards = groups
    .map(({ key: groupKey, title }) => {
      const groupSpecs = specs.filter((spec) => spec.group === groupKey)
      if (groupSpecs.length === 0) return ''
      return `<section class="panel cfg-card">
          <div class="panel-head"><span class="panel-title">${esc(title)}</span></div>
          <div class="panel-body">${groupSpecs
            .map((spec) => controlHtml(spec, values[spec.key], `cfg-${spec.key}`, overriddenKeys.has(spec.key)))
            .join('<div class="cfg-sep"></div>')}</div>
        </section>`
    })
    .join('')
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

function updateNote(view) {
  overriddenKeys = new Set(Object.keys(view.overrides || {}))
  $('deviceNote').textContent = view.overrides
    ? `修订 ${view.revision} · 已覆盖 ${overriddenKeys.size} 项`
    : `修订 ${view.revision} · 全部继承`
}

async function loadDevice() {
  if (!store.deviceId) {
    $('configForm').innerHTML = '<div class="empty">请先在顶栏选择设备</div>'
    $('deviceNote').textContent = ''
    return
  }
  try {
    const view = await api.deviceConfig(store.deviceId)
    updateNote(view)
    renderForm($('configForm'), view.config)
  } catch (error) {
    report('configResult', problemText(error), 'err')
  }
}

async function saveDevice() {
  if (!store.deviceId) return
  report('configResult', '正在保存…', 'info')
  try {
    const result = await api.saveDeviceConfig(store.deviceId, readForm($('configForm')))
    report('configResult', `已保存（修订 ${result.revision}）${result.applied ? '，设备已生效' : `，${result.reason || '未生效'}`}`, 'ok')
    await loadDevice()
  } catch (error) {
    report('configResult', problemText(error), 'err')
  }
}

/** 删除单个键的设备覆盖，回落到全局值（业界标准的 reset-to-inherited 交互）。 */
async function resetKey(key) {
  if (!store.deviceId) return
  try {
    const current = await api.deviceConfig(store.deviceId)
    const overrides = current.overrides || {}
    if (!(key in overrides)) return
    delete overrides[key]
    await api.saveDeviceConfig(store.deviceId, overrides)
    toast('已回落到全局值')
    await loadDevice()
  } catch (error) {
    report('configResult', problemText(error), 'err')
  }
}

/** 全局默认的编辑入口：独立对话框，改的是"所有设备的出厂值"，与设备视图分离。 */
function openGlobalDialog() {
  const input = window.prompt(
    '修改全局默认：输入 JSON（只提交要改的键）。',
    JSON.stringify(globalEffective, null, 2),
  )
  if (input === null) return
  let config
  try {
    config = JSON.parse(input)
  } catch (error) {
    report('configResult', `JSON 解析失败：${error.message}`, 'err')
    return
  }
  report('configResult', '正在保存全局默认…', 'info')
  api
    .saveGlobalConfig(config)
    .then(async (result) => {
      const notified = result.notified || []
      const applied = notified.filter((item) => item.applied).length
      report('configResult', `全局默认已保存（修订 ${result.revision}），推送 ${notified.length} 台、生效 ${applied} 台`, 'ok')
      await loadDevice()
    })
    .catch((error) => report('configResult', problemText(error), 'err'))
}

let dispose = disposer()

export const configView = {
  id: 'config',
  title: '配置',

  mount(root) {
    root.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">设备配置</span>
          <span class="panel-note" id="deviceNote"></span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnGlobal" title="编辑所有设备的全局默认值">全局默认…</button>
            <button type="button" class="btn btn-sm" id="btnSaveDevice">保存并推送</button>
          </div>
        </div>
        <div class="panel-body">
          <div class="muted" style="margin-bottom:8px">表单是这台设备当前生效的值；标记「继承」的键跟随全局默认，改动即成为本设备覆盖（可随时回落）。</div>
          <div id="configForm"><div class="empty">正在加载…</div></div>
        </div>
      </section>
      <div class="result" id="configResult" style="padding:0 4px"></div>`

    dispose = disposer()
    dispose.add(subscribe('device', loadDevice))
    dispose.add(subscribe('view', (id) => { if (id !== 'config') dispose.dispose() }))
    dispose.add(listen($('btnSaveDevice'), 'click', saveDevice))
    dispose.add(listen($('btnGlobal'), 'click', openGlobalDialog))

    // 回落按钮走事件委托：表单整体重建后监听器仍在
    dispose.add(
      listen($('configForm'), 'click', (event) => {
        const button = event.target.closest('[data-reset]')
        if (button) resetKey(button.dataset.reset)
      }),
    )

    api
      .configSpec()
      .then(async (view) => {
        groups = view.groups
        specs = view.specs
        globalEffective = (await api.globalConfig()).config
        await loadDevice()
      })
      .catch((error) => report('configResult', problemText(error), 'err'))
  },

  unmount() {
    dispose.dispose()
  },
}
