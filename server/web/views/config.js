/**
 * 配置：全局为主、设备覆盖为辅，小卡片分模块网格。
 *
 * ## 主辅结构（业界标准的设置继承模式）
 *
 * 全局默认是主编辑位——它就是"配置"二字的默认含义；设备覆盖是辅助，
 * 同页切换，不是平铺的第二份表单。两个视图用同一套卡片表单控件（一套实现，
 * 两种数据源），切换按钮（全部设备 / 当前设备）决定编辑的是哪一级。
 *
 * 设备视图下每个键带「继承/覆盖」徽标：继承跟随全局，改动即成为本设备覆盖，
 * 覆盖态可一键回落（reset-to-inherited）。
 *
 * ## 小卡片网格
 *
 * 一个功能模块一张小卡（不整页一张大卡），auto-fill 网格自动多列排布；
 * 卡内键少（2~7 个），一眼看完一个模块。
 *
 * ## 控件形态按 kind
 *
 * bool → 勾选框；int → 数字输入（带上下限与单位）；text → 文本框；
 * text_map（如学生凭证）→ "键=值" 行编辑器。
 *
 * 表单由服务端登记表生成（`GET /api/v1/config/spec`），键名/类型/范围/分组不在
 * 前端另抄一份。
 */
import { api } from '../core/api.js'
import { problemText, report } from '../core/outcome.js'
import { $, disposer, esc, listen } from '../core/dom.js'
import { store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

let groups = []
let specs = []
/** 分区清单（业务功能/运行基础），来自服务端登记表。 */
let SECTIONS = []
/** 设备覆盖项的键集合（设备视图下其余键处于继承态）。 */
let overriddenKeys = new Set()
/** 编辑目标：global（主）或 device（辅）。 */
let target = 'global'

function badge(isOverridden) {
  return isOverridden
    ? '<span class="cfg-badge cfg-override">覆盖</span>'
    : '<span class="cfg-badge cfg-inherit">继承</span>'
}

function controlHtml(spec, value, id, isOverridden) {
  const desc = `<div class="cfg-desc">${esc(spec.description)}</div>`
  const badgeHtml = target === 'device' ? badge(isOverridden) : ''
  // 单项恢复默认：值等于登记表默认值的键，服务端保存时会自动丢弃覆盖——
  // 恢复即"重填默认值"，与当前视图的作用目标（全局/设备）一致
  const resetButton =
    `<button type="button" class="btn btn-ghost btn-sm cfg-reset" data-reset-one="${esc(spec.key)}" title="恢复这一项为默认值">恢复默认</button>`
  if (spec.kind === 'bool') {
    const checked = value ? 'checked' : ''
    return `<label class="field-row" for="${esc(id)}">
        <input id="${esc(id)}" type="checkbox" data-key="${esc(spec.key)}" data-kind="bool" ${checked}>
        <span>${esc(spec.title)}${badgeHtml}</span>${resetButton}
      </label>${desc}`
  }
  if (spec.kind === 'text_map') {
    const lines = Object.entries(value || {})
      .map(([mapKey, mapValue]) => `${mapKey}=${mapValue}`)
      .join('\n')
    return `<div class="field">
        <label for="${esc(id)}">${esc(spec.title)}${badgeHtml}${resetButton}
          <span class="muted"> · 一行一个：学号=密码</span></label>
        <textarea id="${esc(id)}" data-key="${esc(spec.key)}" data-kind="text_map" rows="3"
                  spellcheck="false" placeholder="学号=密码">${esc(lines)}</textarea>
      </div>${desc}`
  }
  if (spec.kind === 'text') {
    return `<div class="field">
        <label for="${esc(id)}">${esc(spec.title)}${badgeHtml}${resetButton}</label>
        <input id="${esc(id)}" type="text" data-key="${esc(spec.key)}" data-kind="text"
               value="${esc(value)}" spellcheck="false">
      </div>${desc}`
  }
  const min = spec.minimum === null ? '' : `min="${spec.minimum}"`
  const max = spec.maximum === null ? '' : `max="${spec.maximum}"`
  return `<div class="field">
      <label for="${esc(id)}">${esc(spec.title)}${spec.unit ? `（${esc(spec.unit)}）` : ''}${badgeHtml}${resetButton}</label>
      <input id="${esc(id)}" type="number" data-key="${esc(spec.key)}" data-kind="int"
             value="${esc(value)}" ${min} ${max}>
    </div>${desc}`
}

/** 单键恢复默认：重填 spec.default（后续保存时服务端自动丢弃该键的覆盖）。 */
function resetOne(key) {
  const spec = specs.find((item) => item.key === key)
  if (!spec) return
  const input = $(`cfg-${key}`)
  if (spec.kind === 'bool') input.checked = Boolean(spec.default)
  else if (spec.kind === 'text_map') input.value = ''
  else input.value = spec.default
  toast(`已将「${spec.title}」恢复为默认值（尚未保存）`)
}

/** 整组恢复默认：重填该组全部键。 */
function resetGroup(groupKey) {
  for (const spec of specs.filter((item) => item.group === groupKey)) {
    resetOne(spec.key)
  }
}

/** 全部恢复默认：整表重填默认值（记得保存才生效）。 */
function resetAll() {
  if (!window.confirm('将表单中的全部配置项恢复为默认值？（仍需点击「保存」方可生效）')) return
  for (const spec of specs) resetOne(spec.key)
  toast('已将全部配置项恢复为默认值（尚未保存）')
}

function renderForm(values) {
  const used = new Set()
  // 先分区（基础能力/拓展功能），区内再排分组卡片
  const cards = SECTIONS
    .map(([sectionKey, sectionTitle]) => {
      const groupKeys = groups.filter((group) => group.section === sectionKey)
      const groupCards = groupKeys
        .map(({ key: groupKey, title }) => {
          const groupSpecs = specs.filter((spec) => spec.group === groupKey)
          if (groupSpecs.length === 0) return ''
          groupSpecs.forEach((spec) => used.add(spec.key))
          return `<section class="cfg-card">
          <div class="cfg-card-title">
            ${esc(title)}
            <button type="button" class="btn btn-ghost btn-sm cfg-reset" data-reset-group="${esc(groupKey)}" title="将该模块的全部配置项恢复为默认值">恢复默认</button>
          </div>
          ${groupSpecs.map((spec) => controlHtml(spec, values[spec.key], `cfg-${spec.key}`, overriddenKeys.has(spec.key))).join('')}
        </section>`
        })
        .join('')
      if (!groupCards) return ''
      return `<div class="cfg-section"><div class="cfg-section-title">${esc(sectionTitle)}</div><div class="cfg-grid">${groupCards}</div></div>`
    })
    .join('')
  const orphans = specs.filter((spec) => !used.has(spec.key))
  if (orphans.length > 0) {
    throw new Error(`配置键缺少分组：${orphans.map((spec) => spec.key).join('、')}`)
  }
  $('configGrid').innerHTML = cards
}

function parseTextMap(text) {
  const result = {}
  for (const line of text.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const eq = trimmed.indexOf('=')
    if (eq <= 0) throw new Error(`格式错误：${trimmed}（应为「学号=密码」）`)
    result[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim()
  }
  return result
}

function readForm() {
  const config = {}
  for (const input of $('configGrid').querySelectorAll('[data-key]')) {
    const key = input.dataset.key
    if (input.dataset.kind === 'bool') {
      config[key] = input.checked
    } else if (input.dataset.kind === 'text_map') {
      config[key] = parseTextMap(input.value)
    } else if (input.dataset.kind === 'text') {
      config[key] = input.value
    } else {
      const value = Number(input.value)
      if (Number.isFinite(value)) config[key] = value
    }
  }
  return config
}

/** 当前编辑目标的有效值；device 视图下同时更新继承/覆盖标记。 */
async function reload() {
  try {
    if (target === 'global') {
      const view = await api.globalConfig()
      overriddenKeys = new Set()
      renderForm(view.config)
      $('cfgNote').textContent = `已保存 ${view.revision} 次 · 作用于所有设备`
      $('btnSave').textContent = '保存并推送全部'
      return
    }
    if (!store.deviceId) {
      $('configGrid').innerHTML = '<div class="empty">请先在顶栏选择设备</div>'
      $('cfgNote').textContent = ''
      return
    }
    const view = await api.deviceConfig(store.deviceId)
    overriddenKeys = new Set(Object.keys(view.overrides || {}))
    renderForm(view.config)
    $('cfgNote').textContent = view.overrides
      ? `${store.deviceId} · ${overriddenKeys.size} 项单独设置，其余跟随全局`
      : `${store.deviceId} · 全部跟随全局`
    $('btnSave').textContent = '保存至当前设备'
  } catch (error) {
    report('configResult', problemText(error), 'err')
  }
}

async function save() {
  report('configResult', '正在保存…', 'info')
  try {
    const values = readForm()
    if (target === 'global') {
      const result = await api.saveGlobalConfig(values)
      const notified = result.notified || []
      const applied = notified.filter((item) => item.applied).length
      report('configResult', `已保存并推送：${notified.length} 台设备已接收，${applied} 台已生效`, 'ok')
    } else {
      const result = await api.saveDeviceConfig(store.deviceId, values)
      report('configResult', result.applied ? '已保存，设备已生效' : `已保存，${result.reason || '设备暂未生效'}`, 'ok')
    }
    await reload()
  } catch (error) {
    report('configResult', problemText(error), 'err')
  }
}

/**
 * 切换编辑目标：global（全部设备）或 device（单台覆盖）。切换后整表重载。
 */
function setTarget(next) {
  if (target === next) return
  target = next
  document.querySelectorAll('.cfg-tab').forEach((tab) => {
    tab.classList.toggle('is-active', tab.dataset.target === target)
  })
  reload()
}

let dispose = disposer()

export const configView = {
  id: 'config',
  title: '配置',

  mount(root) {
    root.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <span class="cfg-tab is-active" data-target="global">全部设备（全局默认）</span>
          <span class="cfg-tab" data-target="device">单台设备（覆盖）</span>
          <span class="panel-note" id="cfgNote"></span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnResetAll">全部恢复默认</button>
            <button type="button" class="btn btn-sm" id="btnSave">保存</button>
          </div>
        </div>
        <div class="panel-body">
          <div class="muted" style="margin-bottom:8px">全局默认为主，设备覆盖为辅。在单台设备视图下，标记「继承」的配置项跟随全局默认值，修改后即成为该设备的覆盖项，可恢复为继承。各模块与单项均提供「恢复默认」，恢复后需点击「保存」方可生效。</div>
          <div id="configGrid"><div class="empty">正在加载…</div></div>
        </div>
      </section>
      <div class="result" id="configResult" style="padding:0 4px"></div>`

    dispose = disposer()
    dispose.add(subscribe('device', () => { if (target === 'device') reload() }))
    dispose.add(subscribe('view', (id) => { if (id !== 'config') dispose.dispose() }))
    dispose.add(listen($('btnSave'), 'click', save))
    dispose.add(listen($('btnResetAll'), 'click', resetAll))

    root.querySelectorAll('.cfg-tab').forEach((tab) => {
      dispose.add(listen(tab, 'click', () => setTarget(tab.dataset.target)))
    })

    // 模块/单项恢复默认走事件委托：网格整体重建后监听器仍在
    dispose.add(
      listen($('configGrid'), 'click', (event) => {
        const group = event.target.closest('[data-reset-group]')
        if (group) {
          resetGroup(group.dataset.resetGroup)
          return
        }
        const one = event.target.closest('[data-reset-one]')
        if (one) resetOne(one.dataset.resetOne)
      }),
    )

    api
      .configSpec()
      .then((view) => {
        SECTIONS = view.sections.map((section) => [section.key, section.title])
        groups = view.groups
        specs = view.specs
        return reload()
      })
      .catch((error) => report('configResult', problemText(error), 'err'))
  },

  unmount() {
    dispose.dispose()
  },
}
