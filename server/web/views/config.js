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
/** 分区清单（服务端业务/设备下发），来自服务端登记表。 */
let SECTIONS = []
/** 当前页签的分区键；空串表示登记表还没到位。 */
let section = ''
/** 设备覆盖项的键集合（设备视图下其余键处于继承态）。 */
let overriddenKeys = new Set()
/** 编辑目标：global（主）或 device（辅）。只在支持按设备区分的分区里有意义。 */
let target = 'global'

/**
 * 这个分区能不能按设备区分：它的键里有设备 scope 的才行。
 *
 * 服务端的键在服务器上运行、不下发，也就没有"这台设备单独设一个值"这回事——服务端那页
 * 因此不出现设备选择与继承/覆盖标记。判据取自登记表的 `scope`，不在前端硬编码分区名。
 */
function sectionIsPerDevice(sectionKey) {
  const groupKeys = new Set(groups.filter((group) => group.section === sectionKey).map((group) => group.key))
  return specs.some((spec) => groupKeys.has(spec.group) && spec.scope === 'device')
}

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
  if (spec.kind === 'time') {
    return `<div class="field">
        <label for="${esc(id)}">${esc(spec.title)}${badgeHtml}${resetButton}</label>
        <input id="${esc(id)}" type="time" data-key="${esc(spec.key)}" data-kind="time"
               value="${esc(value)}" step="60">
      </div>${desc}`
  }
  if (spec.kind === 'int') {
    const min = spec.minimum === null ? '' : `min="${spec.minimum}"`
    const max = spec.maximum === null ? '' : `max="${spec.maximum}"`
    return `<div class="field">
        <label for="${esc(id)}">${esc(spec.title)}${spec.unit ? `（${esc(spec.unit)}）` : ''}${badgeHtml}${resetButton}</label>
        <input id="${esc(id)}" type="number" data-key="${esc(spec.key)}" data-kind="int"
               value="${esc(value)}" ${min} ${max}>
      </div>${desc}`
  }
  // 新增了 kind 却没写控件，在这里当场报错而不是落进一个默认出口：静默渲染成别的形状
  // 会让人看到一个"看起来正常"的输入框，而它的值根本送不出去。与 renderForm 那句
  // 「配置键缺少分组」同一种做法——宁可页面打不开，也不要它看起来是对的。
  throw new Error(`控件未实现：${spec.key}（kind=${spec.kind}）`)
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
      // 非当前分区只是隐藏，**不从 DOM 里去掉**：保存时 readForm 要收全部键——服务端是
      // 覆盖式写入（`store.put_global_overrides`），只提交一半会把另一半的覆盖当成已删除。
      const hidden = sectionKey === section ? '' : ' hidden'
      return `<div class="cfg-section" data-section="${esc(sectionKey)}"${hidden}><div class="cfg-section-title">${esc(sectionTitle)}</div><div class="cfg-grid">${groupCards}</div></div>`
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
    const kind = input.dataset.kind
    // 只有 int 要转数字、bool 要看勾选、text_map 要拆行，**其余一律取原文**：把"字符串"
    // 当默认出口，将来加一个文本类 kind 时不会因为漏了一个分支而被静默丢掉值
    if (kind === 'bool') config[key] = input.checked
    else if (kind === 'text_map') config[key] = parseTextMap(input.value)
    else if (kind === 'int') {
      const value = Number(input.value)
      if (Number.isFinite(value)) config[key] = value
    } else config[key] = input.value
  }
  return config
}

/** 页签、按钮文案与那句说明：都跟着（分区 + 编辑目标）走，一处写完。 */
function refreshChrome() {
  const perDevice = sectionIsPerDevice(section)
  $('cfgSections').innerHTML = SECTIONS.map(
    ([key, title]) =>
      `<span class="cfg-tab${key === section ? ' is-active' : ''}" data-section="${esc(key)}">${esc(title)}</span>`,
  ).join('')
  $('cfgTargets').innerHTML = perDevice
    ? `<span class="cfg-tab${target === 'global' ? ' is-active' : ''}" data-target="global">全部设备（全局默认）</span>
       <span class="cfg-tab${target === 'device' ? ' is-active' : ''}" data-target="device">单台设备（覆盖）</span>`
    : ''
  $('cfgHint').textContent = perDevice
    ? '设备下发的配置：全局默认为主，设备覆盖为辅。标记「继承」的项跟随全局默认值，改过即成为该设备的覆盖项，可恢复为继承。各模块与单项都提供「恢复默认」，恢复后需点「保存」才生效。'
    : '服务端业务：这些在服务器上运行，不下发设备、也不按设备区分。点「保存」即刻生效。'
}

/** 切分区；切到不按设备区分的分区时把编辑目标收回全局。 */
function setSection(next) {
  if (section === next) return
  section = next
  if (!sectionIsPerDevice(section)) target = 'global'
  reload()
}

/**
 * 重载当前（分区 + 编辑目标）的表单值、标记与按钮文案。
 *
 * `target === 'device'` 只在支持按设备区分的分区里成立——服务端那页没有单台覆盖。
 */
async function reload() {
  try {
    if (sectionIsPerDevice(section) && target === 'device') {
      if (!store.deviceId) {
        $('configGrid').innerHTML = '<div class="empty">请先在顶栏选择设备</div>'
        $('cfgNote').textContent = ''
        $('btnSave').textContent = '保存'
        refreshChrome()
        return
      }
      const view = await api.deviceConfig(store.deviceId)
      overriddenKeys = new Set(Object.keys(view.overrides || {}))
      renderForm(view.config)
      $('cfgNote').textContent = view.overrides
        ? `${store.deviceId} · ${overriddenKeys.size} 项单独设置，其余跟随全局`
        : `${store.deviceId} · 全部跟随全局`
      $('btnSave').textContent = '保存至当前设备'
      refreshChrome()
      return
    }
    const view = await api.globalConfig()
    overriddenKeys = new Set()
    renderForm(view.config)
    $('cfgNote').textContent = sectionIsPerDevice(section)
      ? `已保存 ${view.revision} 次 · 作用于所有设备`
      : `已保存 ${view.revision} 次 · 在服务端运行，不下发设备`
    $('btnSave').textContent = sectionIsPerDevice(section) ? '保存并推送全部' : '保存'
    refreshChrome()
  } catch (error) {
    report('configResult', problemText(error), 'err')
  }
}

async function save() {
  report('configResult', '正在保存…', 'info')
  try {
    const values = readForm()
    if (sectionIsPerDevice(section) && target === 'device') {
      const result = await api.saveDeviceConfig(store.deviceId, values)
      report('configResult', result.applied ? '已保存，设备已生效' : `已保存，${result.reason || '设备暂未生效'}`, 'ok')
    } else {
      const result = await api.saveGlobalConfig(values)
      const notified = result.notified || []
      const applied = notified.filter((item) => item.applied).length
      // 服务端那页保存时设备侧没有变化，服务端就不推送（见 `put_global_config`），
      // 所以这里也不能一律写"已推送"——那句话在服务端那页会是不实的。
      report(
        'configResult',
        notified.length === 0
          ? '已保存 · 设备侧没有变化，未推送'
          : `已保存并推送：${notified.length} 台设备已接收，${applied} 台已生效`,
        'ok',
      )
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
          <span class="cfg-tabs" id="cfgSections"></span>
          <span class="cfg-tabs" id="cfgTargets"></span>
          <span class="panel-note" id="cfgNote"></span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnResetAll">全部恢复默认</button>
            <button type="button" class="btn btn-sm" id="btnSave">保存</button>
          </div>
        </div>
        <div class="panel-body">
          <div class="muted" id="cfgHint" style="margin-bottom:8px"></div>
          <div id="configGrid"><div class="empty">正在加载…</div></div>
        </div>
      </section>
      <div class="result" id="configResult" style="padding:0 4px"></div>`

    dispose = disposer()
    dispose.add(subscribe('device', () => { if (sectionIsPerDevice(section) && target === 'device') reload() }))
    dispose.add(subscribe('view', (id) => { if (id !== 'config') dispose.dispose() }))
    dispose.add(listen($('btnSave'), 'click', save))
    dispose.add(listen($('btnResetAll'), 'click', resetAll))

    // 页签整体重建，所以用委托而不是逐个挂监听
    dispose.add(
      listen($('cfgSections'), 'click', (event) => {
        const tab = event.target.closest('.cfg-tab')
        if (tab) setSection(tab.dataset.section)
      }),
    )
    dispose.add(
      listen($('cfgTargets'), 'click', (event) => {
        const tab = event.target.closest('.cfg-tab')
        if (tab) setTarget(tab.dataset.target)
      }),
    )

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
        SECTIONS = view.sections.map((item) => [item.key, item.title])
        groups = view.groups
        specs = view.specs
        section = SECTIONS.length > 0 ? SECTIONS[0][0] : ''
        return reload()
      })
      .catch((error) => report('configResult', problemText(error), 'err'))
  },

  unmount() {
    dispose.dispose()
  },
}
