/**
 * 配置：全局默认与设备覆盖。
 *
 * 表单**由服务端的登记表生成**（`GET /ops/config/spec`），不在这里再抄一份键名、
 * 类型与取值范围——抄一份就是多一个会漂移的事实源，加一个配置项要改两处，而漏改
 * 的那一处不报错，只是那个键从此改不动。
 *
 * 取值是三级合并：**默认 ← 全局覆盖 ← 设备覆盖**（见 `config/store.py`）。
 * 页面分左右两栏正是后面两级：左边改全局，右边改当前设备。
 */
import { api } from '../core/api.js'
import { $, disposer, esc, listen } from '../core/dom.js'
import { store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

let specs = []

function control(spec, value, prefix) {
  const id = `${prefix}-${spec.key}`
  if (spec.kind === 'bool') {
    const checked = value ? 'checked' : ''
    return `<label class="field-row" for="${esc(id)}" style="margin-bottom:4px">
        <input id="${esc(id)}" type="checkbox" data-key="${esc(spec.key)}" data-kind="bool" ${checked}>
        <span>${esc(spec.title)}</span>
      </label>`
  }
  const min = spec.minimum === null ? '' : `min="${spec.minimum}"`
  const max = spec.maximum === null ? '' : `max="${spec.maximum}"`
  return `<div class="field">
      <label for="${esc(id)}">${esc(spec.title)}${spec.unit ? `（${esc(spec.unit)}）` : ''}</label>
      <input id="${esc(id)}" type="number" data-key="${esc(spec.key)}" data-kind="int"
             value="${esc(value)}" ${min} ${max}>
    </div>`
}

function renderForm(container, values, prefix) {
  container.innerHTML = specs
    .map((spec) => {
      const described = `<div class="muted" style="font-size:11px">${esc(spec.description)}</div>`
      return control(spec, values[spec.key], prefix) + described
    })
    .join('')
}

function readForm(container) {
  const config = {}
  for (const input of container.querySelectorAll('[data-key]')) {
    const key = input.dataset.key
    if (input.dataset.kind === 'bool') config[key] = input.checked
    else {
      const value = Number(input.value)
      if (Number.isFinite(value)) config[key] = value
    }
  }
  return config
}

function report(id, text, kind) {
  const box = $(id)
  box.className = `result ${kind}`
  box.textContent = text
}

async function loadGlobal() {
  try {
    const view = await api.globalConfig()
    renderForm($('globalForm'), view.config, 'g')
    $('globalNote').textContent = `修订 ${view.revision}`
  } catch (error) {
    report('globalResult', error.message, 'err')
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
    report('deviceResult', error.message, 'err')
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
    report('globalResult', error.message, 'err')
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
    report('deviceResult', error.message, 'err')
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
    report('deviceResult', error.message, 'err')
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
      .then((list) => {
        specs = list
        return Promise.all([loadGlobal(), loadDevice()])
      })
      .catch((error) => report('globalResult', `读取配置登记表失败：${error.message}`, 'err'))
  },

  unmount() {
    dispose.dispose()
  },
}
