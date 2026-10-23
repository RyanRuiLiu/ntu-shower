/**
 * 语音播报：按「设备 + 学号 + 事件」匹配规则，规则文本支持可靠占位符。
 *
 * 事件中文名取喷头状态的正名（`DoorState` 的目标状态）：等待开阀 / 淋浴中 /
 * 已暂停 / 淋浴中 / 空闲。同一事件只播优先级最小的一条。
 *
 * 延迟从事件发生时刻算起：浴室 App 自己会播结算提示，延迟用来错开它；
 * 具体值按现场实测调整，播报记录里保留事件时刻与下发时刻。
 *
 * 页面只经 core/api.js 出网，不直接 fetch；表单与表格沿用项目已有的
 * panel / field / table / badge / btn 结构。
 */
import { api, hasIdentity } from '../core/api.js'
import { fmtDateTime } from '../core/clock.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { problemText, report, statusBadgeClass, statusLabel } from '../core/outcome.js'
import { store } from '../core/store.js'
import { toast } from '../core/toast.js'

let spec = null
let events = []
let placeholders = []
let devices = []
let rules = []
let deliveries = []
let status = null
let editingId = null
let previewAudio = null
let dispose = disposer()

function eventItem(value) {
  return events.find((item) => item.value === value) || { value, label: value, description: '' }
}

function deviceName(deviceId) {
  const device = devices.find((item) => item.device_id === deviceId)
  return device ? device.name || device.device_id : deviceId
}

function fmtDelay(ms) {
  const seconds = Number(ms || 0) / 1000
  return `${seconds % 1 === 0 ? seconds : seconds.toFixed(1)} 秒`
}

function deliveryBadge(value) {
  if (value === 'skipped') return '<span class="badge badge-amber">跳过</span>'
  return `<span class="badge ${statusBadgeClass(value)}">${esc(statusLabel(value))}</span>`
}

function renderStatus() {
  if (!status) {
    $('voiceStatus').innerHTML = '<div class="empty">还没有读到语音合成状态</div>'
    return
  }
  const parts = [
    ['模型', status.model_present],
    ['Piper', status.piper_available],
    ['espeak-ng', status.espeak_available],
    ['ffmpeg', status.ffmpeg_available],
  ]
  const badges = parts
    .map(
      ([name, ok]) =>
        `<span class="badge ${ok ? 'badge-green' : 'badge-red'}">${esc(name)} ${ok ? '已就绪' : '缺失'}</span>`,
    )
    .join(' ')
  $('voiceStatus').innerHTML = `<div class="btn-row">${badges}<span class="badge badge-blue">音频 ${esc(String(status.clip_count))} 个</span></div>
    <div class="muted" style="margin-top:8px">${status.ready ? '语音合成就绪。' : '语音合成未就绪：规则保存会失败。'}
    同一设备、学号、事件只播优先级最小的一条；延迟从事件发生时刻算起。</div>`
}

function renderSpec() {
  $('voiceDevice').innerHTML = devices
    .map(
      (device) =>
        `<option value="${esc(device.device_id)}">${esc(device.name || device.device_id)}${device.online ? '（在线）' : '（离线）'}</option>`,
    )
    .join('')
  $('voiceEvent').innerHTML = events
    .map(
      (item) =>
        `<option value="${esc(item.value)}">${esc(item.label)}（${esc(item.description)}）</option>`,
    )
    .join('')
  $('voicePlaceholders').innerHTML = placeholders
    .map(
      (item) =>
        `<div><code>{${esc(item.key)}}</code> ${esc(item.label)}：${esc(item.description)}</div>`,
    )
    .join('')
  if (spec) {
    $('voiceDelay').min = String(spec.delay.min_ms / 1000)
    $('voiceDelay').max = String(spec.delay.max_ms / 1000)
    $('voiceDelay').step = '0.5'
    $('voiceVolume').min = String(spec.volume.minimum)
    $('voiceVolume').max = String(spec.volume.maximum)
    $('voiceVolume').value = String(spec.volume.default)
    $('voicePriority').min = String(spec.priority.minimum)
    $('voicePriority').max = String(spec.priority.maximum)
    $('voicePriority').value = String(spec.priority.default)
  }
}

function renderTable() {
  $('voiceCount').textContent = rules.length > 0 ? `共 ${rules.length} 条` : ''
  if (rules.length === 0) {
    $('voiceTable').innerHTML = '<div class="empty">暂无语音规则</div>'
    return
  }
  const rows = rules
    .map((rule) => {
      const event = eventItem(rule.event)
      const selected = editingId === rule.id ? ' class="is-selected"' : ''
      return `<tr${selected} data-id="${esc(String(rule.id))}">
        <td>${esc(deviceName(rule.device_id))}</td>
        <td><code>${esc(rule.cert_no)}</code></td>
        <td><div>${esc(event.label)}</div><div class="muted">${esc(event.description)}</div></td>
        <td><div>${esc(rule.text)}</div><div class="muted">示例：${esc(rule.sample_text || PLACEHOLDER)}</div></td>
        <td class="mono">${esc(fmtDelay(rule.delay_ms))}</td>
        <td class="mono">${esc(String(rule.volume))}</td>
        <td class="mono">${esc(String(rule.priority))}</td>
        <td>${rule.enabled ? '<span class="badge badge-green">启用</span>' : '<span class="badge">停用</span>'}</td>
        <td>
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="edit">编辑</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="preview">试听</button>
            <button type="button" class="btn btn-danger btn-sm" data-act="delete">删除</button>
          </div>
        </td>
      </tr>`
    })
    .join('')
  $('voiceTable').innerHTML = `<table class="table"><thead><tr>
      <th>设备</th><th>学号</th><th>事件</th><th>播报内容</th><th>延迟</th><th>音量</th><th>优先级</th><th>启用</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

function renderDeliveries() {
  $('voiceDeliveryCount').textContent = deliveries.length > 0 ? `最近 ${deliveries.length} 条` : ''
  if (deliveries.length === 0) {
    $('voiceDeliveryTable').innerHTML = '<div class="empty">暂无播报记录</div>'
    return
  }
  const rows = deliveries
    .map((item) => {
      const event = eventItem(item.event)
      return `<tr>
        <td class="mono">${esc(fmtDateTime(item.dispatched_at || item.occurred_at))}</td>
        <td>${esc(deviceName(item.device_id))}</td>
        <td><code>${esc(item.cert_no)}</code></td>
        <td>${esc(event.label)}</td>
        <td class="mono">${esc(String(item.door_no))}</td>
        <td><div title="${esc(item.text || '')}">${esc(item.text || PLACEHOLDER)}</div></td>
        <td class="mono">${esc(fmtDelay(item.delay_ms))}</td>
        <td><span title="${esc(item.detail || '')}">${deliveryBadge(item.status)}</span></td>
      </tr>`
    })
    .join('')
  $('voiceDeliveryTable').innerHTML = `<table class="table"><thead><tr>
      <th>下发时刻</th><th>设备</th><th>学号</th><th>事件</th><th>门号</th><th>播报内容</th><th>延迟</th><th>状态</th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

function applyFormMode() {
  const editing = editingId !== null
  $('voiceFormTitle').textContent = editing ? '编辑语音规则' : '新增语音规则'
  $('btnVoiceSave').textContent = editing ? '保存修改' : '新增'
  $('btnVoiceCancel').hidden = !editing
  $('voiceDevice').disabled = editing
}

function eventHint() {
  const event = eventItem($('voiceEvent').value)
  $('voiceEventHint').textContent = event.description
}

function clearForm() {
  const device = devices.find((item) => item.device_id === store.deviceId) || devices[0]
  $('voiceDevice').value = device ? device.device_id : ''
  $('voiceEvent').value = events[0] ? events[0].value : ''
  $('voiceCert').value = ''
  $('voiceText').value = ''
  $('voiceDelay').value = spec ? String(spec.delay.default_ms / 1000) : '0'
  $('voiceVolume').value = spec ? String(spec.volume.default) : '3'
  $('voicePriority').value = spec ? String(spec.priority.default) : '100'
  $('voiceEnabled').checked = true
  $('voiceSample').hidden = true
  $('voiceSampleText').textContent = ''
  eventHint()
  applyFormMode()
}

function fillForm(rule) {
  $('voiceDevice').value = rule.device_id
  $('voiceCert').value = rule.cert_no
  $('voiceEvent').value = rule.event
  $('voiceText').value = rule.text
  $('voiceDelay').value = String(rule.delay_ms / 1000)
  $('voiceVolume').value = String(rule.volume)
  $('voicePriority').value = String(rule.priority)
  $('voiceEnabled').checked = rule.enabled
  eventHint()
  applyFormMode()
  if (rule.sample_text) {
    $('voiceSampleText').textContent = rule.sample_text
    $('voiceSample').hidden = false
  }
}

function readForm() {
  const deviceId = $('voiceDevice').value
  const certNo = $('voiceCert').value.trim()
  const text = $('voiceText').value.trim()
  const event = $('voiceEvent').value
  if (!deviceId) return report('voiceResult', '失败 · 请选择设备', 'err')
  if (!certNo) return report('voiceResult', '失败 · 请填写学号', 'err')
  if (!event) return report('voiceResult', '失败 · 请选择事件', 'err')
  if (!text) return report('voiceResult', '失败 · 请填写播报内容', 'err')
  return {
    device_id: deviceId,
    cert_no: certNo,
    event,
    text,
    delay_ms: Math.round(Number($('voiceDelay').value) * 1000),
    volume: Number($('voiceVolume').value),
    priority: Number($('voicePriority').value),
    enabled: $('voiceEnabled').checked,
  }
}

function showSample(text) {
  $('voiceSampleText').textContent = text
  $('voiceSample').hidden = false
}

function playPreview(url) {
  stopPreview()
  if (!url) return
  previewAudio = new Audio(url)
  previewAudio.play().catch((error) => toast(`试听失败：${error.message}`))
}

function stopPreview() {
  if (previewAudio) {
    previewAudio.pause()
    previewAudio = null
  }
}

async function previewRule(rule) {
  report('voiceResult', '正在合成试听…', 'info')
  try {
    const result = await api.previewVoiceRule({
      text: rule.text,
      event: rule.event,
      cert_no: rule.cert_no,
      door_no: 1,
      assigned_seconds: 1122,
      shower_seconds: 965,
    })
    showSample(result.rendered_text)
    playPreview(result.clip_url)
    report('voiceResult', '试听已生成；实际播报按事件发生时的值渲染。', 'ok')
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

async function previewForm() {
  const payload = readForm()
  if (!payload) return
  await previewRule({ text: payload.text, event: payload.event, cert_no: payload.cert_no })
}

async function submitForm() {
  const payload = readForm()
  if (!payload) return
  report('voiceResult', '正在保存…', 'info')
  try {
    if (editingId !== null) {
      const { device_id: _deviceId, ...rest } = payload
      await api.updateVoiceRule(editingId, rest)
    } else {
      await api.createVoiceRule(payload)
    }
    editingId = null
    await reload()
    report('voiceResult', '已保存；音频会在首次播报时按最终文本生成并缓存。', 'ok')
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

async function deleteRule(rule) {
  if (!window.confirm(`删除这条规则？\n${rule.cert_no} · ${eventItem(rule.event).label}`)) return
  try {
    await api.deleteVoiceRule(rule.id)
    if (editingId === rule.id) {
      editingId = null
      clearForm()
    }
    await reload()
    toast('已删除')
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

function enterEdit(rule) {
  editingId = rule.id
  fillForm(rule)
  renderTable()
  report('voiceResult', '', 'info')
  $('voiceFormPanel').scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function exitEdit() {
  editingId = null
  clearForm()
  renderTable()
}

async function reload() {
  if (!hasIdentity()) {
    $('voiceStatus').innerHTML = '<div class="empty">语音规则需要管理身份，请在顶栏填写身份标识</div>'
    $('voiceTable').innerHTML = ''
    $('voiceDeliveryTable').innerHTML = ''
    $('voiceCount').textContent = ''
    $('voiceDeliveryCount').textContent = ''
    return
  }
  try {
    const [specResult, ruleList, statusResult, deviceList, deliveryList] = await Promise.all([
      api.voiceSpec(),
      api.voiceRules(),
      api.voiceStatus(),
      api.devices(),
      api.voiceDeliveries(),
    ])
    spec = specResult
    events = specResult.events || []
    placeholders = specResult.placeholders || []
    status = statusResult
    devices = deviceList
    rules = ruleList
    deliveries = deliveryList
    renderSpec()
    renderStatus()
    renderTable()
    renderDeliveries()
    if (editingId !== null) {
      const rule = rules.find((item) => item.id === editingId)
      if (rule) fillForm(rule)
      else exitEdit()
    } else {
      clearForm()
    }
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

export const voiceView = {
  id: 'voice',
  title: '语音播报',

  mount(root) {
    root.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">合成状态</span>
        </div>
        <div class="panel-body" id="voiceStatus"></div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">语音规则</span>
          <span class="panel-note" id="voiceCount"></span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnVoiceReload">刷新</button>
          </div>
        </div>
        <div class="panel-body flush" id="voiceTable"></div>
      </section>

      <section class="panel" id="voiceFormPanel">
        <div class="panel-head">
          <span class="panel-title" id="voiceFormTitle">新增语音规则</span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnVoiceCancel" hidden>取消编辑</button>
          </div>
        </div>
        <div class="panel-body">
          <div class="field">
            <label for="voiceDevice">设备</label>
            <select id="voiceDevice"></select>
          </div>
          <div class="field">
            <label for="voiceCert">学号</label>
            <input id="voiceCert" type="text" class="mono" spellcheck="false" autocomplete="off">
          </div>
          <div class="field">
            <label for="voiceEvent">事件</label>
            <select id="voiceEvent"></select>
            <div class="muted" id="voiceEventHint"></div>
          </div>
          <div class="field">
            <label for="voiceText">播报内容（模板）</label>
            <input id="voiceText" type="text" placeholder="例如：{event}，{door_no} 号喷头">
            <div class="muted" id="voicePlaceholders"></div>
          </div>
          <div class="field">
            <label for="voiceDelay">触发延迟（秒）</label>
            <input id="voiceDelay" type="number" min="0" max="10" step="0.5" value="0">
            <div class="muted">从事件发生时刻算起；浴室 App 自己的语音提示结束后再播，现场实测后调整。</div>
          </div>
          <div class="field">
            <label for="voiceVolume">音量（0–5）</label>
            <input id="voiceVolume" type="number" min="0" max="5" step="1" value="3">
            <div class="muted">0 为静音；播放结束后恢复设备原音量。</div>
          </div>
          <div class="field">
            <label for="voicePriority">优先级（数字小者先）</label>
            <input id="voicePriority" type="number" min="0" max="999" step="1" value="100">
          </div>
          <div class="field">
            <label class="field-row" style="margin:0">
              <input id="voiceEnabled" type="checkbox" checked>
              <span>启用</span>
            </label>
          </div>
          <div class="btn-row">
            <button type="button" class="btn btn-sm" id="btnVoiceSave">新增</button>
            <button type="button" class="btn btn-ghost btn-sm" id="btnVoicePreview">试听示例</button>
          </div>
          <div class="field" id="voiceSample" hidden>
            <label>示例播报</label>
            <div class="muted" id="voiceSampleText"></div>
          </div>
          <div class="result" id="voiceResult"></div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">最近播报</span>
          <span class="panel-note" id="voiceDeliveryCount"></span>
        </div>
        <div class="panel-body flush" id="voiceDeliveryTable"></div>
      </section>`

    dispose = disposer()
    dispose.add(listen($('btnVoiceReload'), 'click', reload))
    dispose.add(listen($('btnVoiceSave'), 'click', submitForm))
    dispose.add(listen($('btnVoicePreview'), 'click', previewForm))
    dispose.add(listen($('btnVoiceCancel'), 'click', exitEdit))
    dispose.add(listen($('voiceEvent'), 'change', eventHint))
    dispose.add(
      listen($('voiceTable'), 'click', async (event) => {
        const button = event.target.closest('button[data-act]')
        if (!button) return
        const id = Number(button.closest('tr').dataset.id)
        const rule = rules.find((item) => item.id === id)
        if (!rule) return
        if (button.dataset.act === 'edit') enterEdit(rule)
        if (button.dataset.act === 'preview') await previewRule(rule)
        if (button.dataset.act === 'delete') await deleteRule(rule)
      }),
    )

    exitEdit()
    reload()
  },

  unmount() {
    stopPreview()
    dispose.dispose()
  },
}
