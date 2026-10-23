/**
 * 语音播报：规则按「设备 × 学号 × 事件」匹配，两个目标维度各自可选「全部」。
 *
 * 事件中文名取喷头状态的正名（`DoorState` 的目标状态）：等待开阀 / 淋浴中 /
 * 已暂停 / 淋浴中 / 空闲。具体规则压过通配规则：先比设备维度，再比学号维度；
 * 一个「设备 × 学号 × 事件」只有一条规则，覆盖切换靠停用具体规则让通配顶上。
 *
 * 延迟从事件发生时刻算起：浴室 App 自己会播结算提示，延迟用来错开它。延迟窗口
 * 同时用于设备端预下载音频；「最近播报」带各阶段耗时（备音频 / 下发 / 收尾），
 * 现场按它调延迟与语速。
 *
 * 页面只经 core/api.js 出网，不直接 fetch；表单与表格沿用项目已有的
 * panel / field / table / badge / btn 结构，设备选择与身份页共用 core/device-scope.js。
 * 占位符说明是可点击的片：点一下插到模板光标处。
 */
import { api, hasIdentity } from '../core/api.js'
import { fmtDateTime } from '../core/clock.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { bindScopeToggle, readScopeEditor, scopeEditorHtml } from '../core/device-scope.js'
import { problemText, report, statusBadgeClass, statusLabel } from '../core/outcome.js'
import { deviceName, refreshDevices, store } from '../core/store.js'
import { toast } from '../core/toast.js'

const WILDCARD = '*'
const ANY_PLACEHOLDER = /\{(fx:|pause:|[a-z_])/

let spec = null
let events = []
let placeholders = []
let rules = []
let deliveries = []
let status = null
let effects = []
let editingId = null
let previewAudio = null
/** 规则表的设备筛选：'' 全部、'*' 通配规则、否则某台设备。 */
let filterDevice = ''
let dispose = disposer()

function eventItem(value) {
  return events.find((item) => item.value === value) || { value, label: value, description: '' }
}

function fmtDelay(ms) {
  const seconds = Number(ms || 0) / 1000
  return `${seconds % 1 === 0 ? seconds : seconds.toFixed(1)} 秒`
}

function fmtMs(ms) {
  const value = Number(ms || 0)
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}秒`
}

function deliveryBadge(value) {
  if (value === 'skipped') return '<span class="badge badge-amber">跳过</span>'
  if (value === 'pending') return '<span class="badge badge-amber">进行中</span>'
  return `<span class="badge ${statusBadgeClass(value)}">${esc(statusLabel(value))}</span>`
}

/** 设备/学号单元格：通配给徽标，具体给名字/学号。 */
function deviceCell(deviceId) {
  if (deviceId === WILDCARD) return '<span class="badge badge-blue">全部设备</span>'
  return esc(deviceName(deviceId))
}

function certCell(certNo, exempt) {
  if (certNo === WILDCARD) {
    const badge = '<span class="badge badge-blue">全部学号</span>'
    if (exempt && exempt.length > 0) {
      return `<div>${badge}</div><div class="muted" title="${esc(exempt.join('、'))}">排除 ${exempt.length} 人</div>`
    }
    return badge
  }
  return `<code>${esc(certNo)}</code>`
}

/** 通配目标的纯文本写法（确认框、提示里用，不带 HTML）。 */
function targetText(rule) {
  const device = rule.device_id === WILDCARD ? '全部设备' : deviceName(rule.device_id)
  const cert = rule.cert_no === WILDCARD ? '全部学号' : rule.cert_no
  const exempt = rule.exempt && rule.exempt.length > 0 ? `（排除 ${rule.exempt.length} 人）` : ''
  return `${device} · ${cert}${exempt} · ${eventItem(rule.event).label}`
}

function renderStatus() {
  if (!status) {
    $('voiceStatus').innerHTML = '<div class="empty">还没有读到语音合成状态</div>'
    return
  }
  const ffmpeg = status.ffmpeg_available
    ? '<span class="badge badge-green">ffmpeg 已就绪</span>'
    : '<span class="badge badge-red">ffmpeg 缺失</span>'
  const rows = (status.voices || [])
    .map(
      (item) => `<tr data-voice="${esc(item.key)}">
        <td class="mono">${esc(item.key)}</td>
        <td>${esc(item.note)}</td>
        <td>${item.available ? '<span class="badge badge-green">可用</span>' : '<span class="badge badge-red">缺失</span>'}</td>
        <td><button type="button" class="btn btn-ghost btn-sm" data-act="audition" ${item.available ? '' : 'disabled'}>试听</button></td>
      </tr>`,
    )
    .join('')
  $('voiceStatus').innerHTML = `<div class="btn-row">${ffmpeg}<span class="badge badge-blue">音频 ${esc(String(status.clip_count))} 个</span></div>
    <table class="table" style="margin-top:8px"><thead><tr><th>音色</th><th>说明</th><th>状态</th><th></th></tr></thead><tbody>${rows}</tbody></table>
    <div class="muted" style="margin-top:8px">音色在每条规则上单独选；具体规则压过通配（先设备、后学号维度），延迟从事件发生时刻算起，窗口内设备并行预下载。</div>`
}

/** 边界一律来自 /voice/spec：输入范围与标签文字都由它渲染，页面不抄第二份。 */
function eventChecksHtml(selected) {
  const phases = [...new Set(events.map((item) => item.phase || '其它'))]
  return phases
    .map((phase) => {
      const boxes = events
        .filter((item) => (item.phase || '其它') === phase)
        .map(
          (item) => `<label class="field-row" style="margin:0 12px 0 0">
              <input type="checkbox" data-event="${esc(item.value)}" ${selected.includes(item.value) ? 'checked' : ''}>
              <span title="${esc(item.description)}">${esc(item.label)}（${esc(item.description)}）</span>
            </label>`,
        )
        .join('')
      return `<div style="margin-bottom:4px"><span class="muted">${esc(phase)}</span><div class="btn-row">${boxes}</div></div>`
    })
    .join('')
}

function readEventSelection() {
  return [...document.querySelectorAll('input[data-event]:checked')].map((input) => input.dataset.event)
}

function renderSpec() {
  // 占位符是可点击的片：正文只留名字，完整说明放 title；点一下插到光标处。
  // 音效片来自音效库（{fx:名称}），停顿片插 {pause:毫秒}
  const chips = [
    ...placeholders.map(
      (item) =>
        `<button type="button" class="ph-chip" data-ph="${esc(item.key)}" title="${esc(item.description)}">{${esc(item.key)}} ${esc(item.label)}</button>`,
    ),
    ...effects.map(
      (item) =>
        `<button type="button" class="ph-chip" data-ph="fx:${esc(item.name)}" title="在该位置插入音效「${esc(item.name)}》（${esc(fmtMs(item.duration_ms))}），只响不读">♪${esc(item.name)}</button>`,
    ),
    '<button type="button" class="ph-chip" data-ph="pause:500" title="在该位置插入 0.5 秒静音；数字可改成 100–5000 的毫秒数">（停顿0.5秒）</button>',
  ]
  $('voicePlaceholders').innerHTML = `${chips.join(" ")}
    <div class="muted" style="margin-top:4px">点一下插到模板光标处，说明悬停可见；音效与停顿只响不读。</div>`
  if (!spec) return
  const delayMin = spec.delay.minimum_ms / 1000
  const delayMax = spec.delay.maximum_ms / 1000
  $('voiceDelayLabel').textContent = `触发延迟（${delayMin}–${delayMax} 秒）`
  $('voiceDelay').min = String(delayMin)
  $('voiceDelay').max = String(delayMax)
  $('voiceDelay').step = '0.5'
  $('voiceVolumeLabel').textContent = `音量（${spec.volume.minimum}–${spec.volume.maximum}）`
  $('voiceVolume').min = String(spec.volume.minimum)
  $('voiceVolume').max = String(spec.volume.maximum)
}

function renderEffects() {
  $('voiceEffectCount').textContent = effects.length > 0 ? `共 ${effects.length} 个` : ''
  if (effects.length === 0) {
    $('voiceEffectTable').innerHTML =
      '<div class="empty">还没有上传音效；支持常见音频格式，会统一转换，时长不超过 10 秒。</div>'
    return
  }
  const rows = effects
    .map(
      (item) => `<tr data-id="${esc(item.effect_id)}">
        <td>${esc(item.name)}</td>
        <td class="mono">${esc(fmtMs(item.duration_ms))}</td>
        <td class="mono">${esc(String(Math.round(item.size_bytes / 1024)))} KB</td>
        <td>
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="audition">试听</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="rename">改名</button>
            <button type="button" class="btn btn-danger btn-sm" data-act="delete">删除</button>
          </div>
        </td>
      </tr>`,
    )
    .join('')
  $('voiceEffectTable').innerHTML = `<table class="table"><thead><tr>
      <th>名称</th><th>时长</th><th>大小</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

async function uploadEffect() {
  const name = $('voiceEffectName').value.trim()
  const file = $('voiceEffectFile').files[0]
  if (!file) return report('voiceEffectResult', '失败 · 请选择音频文件', 'err')
  if (!name) return report('voiceEffectResult', '失败 · 请填写音效名称', 'err')
  const form = new FormData()
  form.append('name', name)
  form.append('file', file)
  report('voiceEffectResult', '正在上传并转换…', 'info')
  try {
    await api.uploadVoiceEffect(form)
    $('voiceEffectName').value = ''
    $('voiceEffectFile').value = ''
    await reload()
    report('voiceEffectResult', '音效已入库；规则的「前奏音效」里就能选到它。', 'ok')
  } catch (error) {
    report('voiceEffectResult', problemText(error), 'err')
  }
}

async function renameEffect(effect) {
  const name = window.prompt('新的音效名称（规则模板里的 {fx:…} 会一并改写）', effect.name)
  if (name === null) return
  const trimmed = name.trim()
  if (!trimmed || trimmed === effect.name) return
  try {
    await api.renameVoiceEffect(effect.effect_id, trimmed)
    await reload()
    toast('已改名')
  } catch (error) {
    report('voiceEffectResult', problemText(error), 'err')
  }
}

async function deleteEffect(effect) {
  if (!window.confirm(`删除音效「${effect.name}」？`)) return
  try {
    await api.deleteVoiceEffect(effect.effect_id)
    await reload()
    toast('已删除')
  } catch (error) {
    report('voiceEffectResult', problemText(error), 'err')
  }
}

/** 规则表单的音色下拉（spec.voices 驱动，展示名 + 说明）。 */
function renderVoiceOptions(selected) {
  const options = (spec && spec.voices ? spec.voices : []).map((item) => [
    item.key,
    `${item.key}（${item.note}）`,
  ])
  if (selected !== undefined && !options.some(([value]) => value === selected)) selected = 'melo'
  $('voiceVoice').innerHTML = options
    .map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`)
    .join('')
  $('voiceVoice').value = selected === undefined ? $('voiceVoice').value : selected
}

/** 规则表的设备筛选下拉：全部 / 通配 / 每台设备。 */
function renderFilter() {
  const options = [
    ['', '全部规则'],
    [WILDCARD, '仅全部设备（通配）'],
    ...store.devices.map((device) => [device.device_id, deviceName(device.device_id)]),
  ]
  // 筛选指向的设备已从档案消失时回到全部，不让下拉停在第一项而语义还是旧值
  if (!options.some(([value]) => value === filterDevice)) filterDevice = ''
  $('voiceFilterDevice').innerHTML = options
    .map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`)
    .join('')
  $('voiceFilterDevice').value = filterDevice
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
        <td>${deviceCell(rule.device_id)}</td>
        <td>${certCell(rule.cert_no, rule.exempt)}</td>
        <td><div>${esc(event.label)}</div><div class="muted">${esc(event.description)}</div></td>
        <td>${esc(rule.text)}</td>
        <td class="mono">${esc(fmtDelay(rule.delay_ms))}</td>
        <td class="mono">${esc(String(rule.volume))}</td>
        <td class="mono">${esc(rule.voice)}</td>
        <td>${rule.enabled ? '<span class="badge badge-green">启用</span>' : '<span class="badge">停用</span>'}</td>
        <td>
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="toggle">${rule.enabled ? '停用' : '启用'}</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="edit">编辑</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="preview">试听</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="push">推设备</button>
            <button type="button" class="btn btn-danger btn-sm" data-act="delete">删除</button>
          </div>
        </td>
      </tr>`
    })
    .join('')
  $('voiceTable').innerHTML = `<table class="table"><thead><tr>
      <th>设备</th><th>学号</th><th>事件</th><th>播报内容</th><th>延迟</th><th>音量</th><th>音色</th><th>启用</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

/**
 * 各阶段耗时：备音频（含排队）、事件→下发、下发→收尾。
 * 未下发的投递没有「发/完」，也不谈设备缓存——缓存徽标只在真播过时才有意义。
 */
function stageCell(item) {
  const dispatched = item.dispatch_offset_ms !== null && item.dispatch_offset_ms !== undefined
  const parts = []
  if (item.status === 'failed' && !dispatched) {
    parts.push('备音频失败')
  } else {
    parts.push(`备${fmtMs(item.clip_ms)}`)
  }
  if (dispatched) parts.push(`发${fmtMs(item.dispatch_offset_ms)}`)
  if (item.finish_offset_ms !== null && item.finish_offset_ms !== undefined) {
    parts.push(`完${fmtMs(item.finish_offset_ms)}`)
  }
  let cache = ''
  if (dispatched) {
    cache = item.downloaded
      ? ' <span class="badge badge-amber">现场下载</span>'
      : ' <span class="badge badge-green">设备缓存</span>'
  }
  const tooltip = '备 = 认领后备齐音频（含排队）；发 = 事件到下发；完 = 下发到设备收尾'
  return `<div class="mono" title="${esc(tooltip)}">${esc(parts.join(' · '))}${cache}</div>`
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
      const dispatched = Boolean(item.dispatched_at)
      const timeCell = dispatched
        ? esc(fmtDateTime(item.dispatched_at))
        : `<span class="muted" title="事件时刻（未下发）">${esc(fmtDateTime(item.occurred_at))}</span>`
      return `<tr>
        <td class="mono">${timeCell}</td>
        <td>${deviceCell(item.device_id)}</td>
        <td>${certCell(item.cert_no)}</td>
        <td>${esc(event.label)}</td>
        <td class="mono">${esc(String(item.door_no))}</td>
        <td><div title="${esc(item.text || '')}">${esc(item.text || PLACEHOLDER)}</div></td>
        <td class="mono">${esc(fmtDelay(item.delay_ms))}</td>
        <td>${stageCell(item)}</td>
        <td><span title="${esc(item.detail || '')}">${deliveryBadge(item.status)}</span></td>
      </tr>`
    })
    .join('')
  $('voiceDeliveryTable').innerHTML = `<table class="table"><thead><tr>
      <th>下发时刻</th><th>设备</th><th>学号</th><th>事件</th><th>喷头号</th><th>播报内容</th><th>延迟</th><th>阶段耗时</th><th>状态</th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

function applyFormMode() {
  const editing = editingId !== null
  $('voiceFormTitle').textContent = editing ? '编辑语音规则' : '新增语音规则'
  $('btnVoiceSave').textContent = editing ? '保存修改' : '新增'
  $('btnVoiceCancel').hidden = !editing
  updateCertHint()
}

/** 学号框的提示随模式变：勾「全部学号」时框里填的是排除名单，否则是目标名单。 */
function updateCertHint() {
  if ($('voiceCertWildcard').checked) {
    $('voiceCertHint').textContent = '排除名单：一行一个学号，这些人不播这条规则（可能命中其它规则）。'
    return
  }
  $('voiceCertHint').textContent = editingId !== null
    ? '编辑时只能填一个学号；批量请另用新增。'
    : '一行一个学号可批量创建；一次最多展开 500 条规则。'
}

function clearForm() {
  $('voiceScope').innerHTML = scopeEditorHtml(store.devices, [store.deviceId || WILDCARD])
  $('voiceCertWildcard').checked = false
  $('voiceCert').disabled = false
  $('voiceCert').value = ''
  $('voiceEvents').innerHTML = eventChecksHtml(events[0] ? [events[0].value] : [])
  renderVoiceOptions('melo')
  $('voiceText').value = ''
  $('voiceDelay').value = spec ? String(spec.delay.default_ms / 1000) : '0'
  $('voiceVolume').value = spec ? String(spec.volume.default) : '3'
  $('voiceEnabled').checked = true
  $('voiceSample').hidden = true
  $('voiceSampleText').textContent = ''
  applyFormMode()
}

function fillForm(rule) {
  $('voiceScope').innerHTML = scopeEditorHtml(store.devices, [rule.device_id])
  const wildcard = rule.cert_no === WILDCARD
  $('voiceCertWildcard').checked = wildcard
  $('voiceCert').disabled = false
  // 勾「全部学号」时框里展示的是排除名单；不勾则是那一个学号
  $('voiceCert').value = wildcard ? (rule.exempt || []).join('\n') : rule.cert_no
  $('voiceEvents').innerHTML = eventChecksHtml([rule.event])
  renderVoiceOptions(rule.voice)
  $('voiceText').value = rule.text
  $('voiceDelay').value = String(rule.delay_ms / 1000)
  $('voiceVolume').value = String(rule.volume)
  $('voiceEnabled').checked = rule.enabled
  applyFormMode()
  showSample(rule.sample_text)
}

/** 学号框按空白与常见分隔符切分（「一行一个」是主形状，贴进逗号分隔也能认）。 */
function readCertLines() {
  const seen = new Set()
  const lines = []
  for (const raw of $('voiceCert').value.split(/[\s,，;；、]+/)) {
    const item = raw.trim()
    if (item && !seen.has(item)) {
      seen.add(item)
      lines.push(item)
    }
  }
  return lines
}

/** 一个框两种含义：勾「全部学号」时行是排除名单，否则是目标名单。 */
function readTargets() {
  if ($('voiceCertWildcard').checked) return { cert_nos: ['*'], exempt: readCertLines() }
  return { cert_nos: readCertLines(), exempt: [] }
}

function readForm() {
  const deviceIds = readScopeEditor($('voiceScope'))
  if (deviceIds === null) return report('voiceResult', '失败 · 请选择设备（或勾选全部设备）', 'err')
  const { cert_nos: certTargets, exempt } = readTargets()
  const wildcardCert = $('voiceCertWildcard').checked
  if (certTargets.length === 0) return report('voiceResult', '失败 · 请填写学号（或勾选全部学号）', 'err')
  const selectedEvents = readEventSelection()
  if (selectedEvents.length === 0) return report('voiceResult', '失败 · 请勾选至少一个事件', 'err')
  const event = selectedEvents[0]
  const text = $('voiceText').value.trim()
  if (!text) return report('voiceResult', '失败 · 请填写播报内容', 'err')
  const common = {
    text,
    delay_ms: Math.round(Number($('voiceDelay').value) * 1000),
    volume: Number($('voiceVolume').value),
    enabled: $('voiceEnabled').checked,
    exempt_cert_nos: exempt,
    voice: $('voiceVoice') ? $('voiceVoice').value : 'melo',
  }
  if (editingId !== null) {
    if (deviceIds.length > 1) return report('voiceResult', '失败 · 编辑时只能选一台设备或全部设备', 'err')
    if (selectedEvents.length > 1) {
      return report('voiceResult', '失败 · 编辑时只能勾选一个事件；多个事件请用新增批量建', 'err')
    }
    // 通配学号是合法的单值目标；手填多个学号只属于新增的批量路径
    if (!wildcardCert && certTargets.length > 1) {
      return report('voiceResult', '失败 · 编辑时只能填一个学号', 'err')
    }
    return { device_id: deviceIds[0], cert_no: certTargets[0], event, ...common }
  }
  return { device_ids: deviceIds, cert_nos: certTargets, events: selectedEvents, ...common }
}

/** 渲染效果区：用示例值展示模板渲染后的样子，引号与说明把它和真实播报区分开。 */
function showSample(text) {
  if (!text) return
  $('voiceSampleText').textContent = text
  $('voiceSample').hidden = false
}

/** 把占位符插到模板输入框的光标处（无光标时追加到末尾），插完光标落在占位符后。 */
function insertPlaceholder(key) {
  const input = $('voiceText')
  const token = `{${key}}`
  const start = input.selectionStart ?? input.value.length
  const end = input.selectionEnd ?? start
  input.value = input.value.slice(0, start) + token + input.value.slice(end)
  const caret = start + token.length
  input.focus()
  input.setSelectionRange(caret, caret)
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

async function pushRule(rule) {
  let deviceId = rule.device_id
  if (deviceId === WILDCARD) {
    // 通配规则对全部设备生效：推给哪台由现场指定（列在线设备，输序号）
    const online = devices.filter((d) => d.online)
    if (!online.length) return report('voiceResult', '失败 · 没有在线设备可推送', 'err')
    const list = online.map((d, i) => `${i + 1}. ${d.name || d.device_id}`).join('\n')
    const picked = window.prompt(`这条规则对全部设备生效。推送到哪台？输入序号：
${list}`, '1')
    if (picked === null) return
    const chosen = online[Number(picked) - 1]
    if (!chosen) return report('voiceResult', '失败 · 序号无效', 'err')
    deviceId = chosen.device_id
  }
  report('voiceResult', `正在推送到 ${deviceName(deviceId)} 播放…`, 'info')
  try {
    const result = await api.pushVoiceRule(rule.id, deviceId)
    showSample(result.rendered_text)
    const ok = result.status === 'ok'
    report('voiceResult', ok ? `已在 ${deviceName(deviceId)} 上播放。` : `设备返回：${result.detail || result.status}`, ok ? 'ok' : 'err')
    await reload()
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

async function previewRule(rule) {
  report('voiceResult', '正在合成试听…', 'info')
  try {
    const result = await api.previewVoiceRule({
      text: rule.text,
      event: rule.event,
      cert_no: rule.cert_no === WILDCARD ? '示例学号' : rule.cert_no,
      door_no: 1,
      voice: rule.voice || 'melo',
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
  const cert = payload.cert_nos ? payload.cert_nos[0] : payload.cert_no
  // 创建形态是 events 数组（可勾多个），预览取第一个；编辑形态是单个 event
  const event = payload.events ? payload.events[0] : payload.event
  await previewRule({ text: payload.text, event, cert_no: cert, voice: payload.voice })
}

/** 保存后的那句话按结果状态说：一条没建成、全部跳过、建成几条，各是各的说法。 */
function createResultMessage(payload, created, skipped) {
  const warmNote = ANY_PLACEHOLDER.test(payload.text)
    ? '带占位符的文本在首次播报时渲染生成'
    : '固定文本的音频已开始预热到设备'
  if (created === 0) return `没有新建：${skipped} 个目标已有规则（含停用的）。`
  const skipNote = skipped > 0 ? `，跳过 ${skipped} 条（目标已有规则）` : ''
  return `已新建 ${created} 条规则${skipNote}；${warmNote}。`
}

async function submitForm() {
  const payload = readForm()
  if (!payload) return
  report('voiceResult', '正在保存…', 'info')
  try {
    if (editingId !== null) {
      await api.updateVoiceRule(editingId, payload)
      const warmNote = ANY_PLACEHOLDER.test(payload.text)
        ? '带占位符的文本在首次播报时渲染生成。'
        : '固定文本的音频已重新预热。'
      report('voiceResult', `已保存；${warmNote}`, 'ok')
    } else {
      const result = await api.createVoiceRule(payload)
      report('voiceResult', createResultMessage(payload, result.created.length, result.skipped.length), 'ok')
      if (result.created.length === 0) return
      // 一个都没建成（目标全已存在）：表单原样留着，调整名单后可直接重试
    }
    editingId = null
    await reload()
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

async function toggleRule(rule) {
  try {
    await api.patchVoiceRule(rule.id, { enabled: !rule.enabled })
    await loadRules()
    toast(rule.enabled ? '已停用' : '已启用')
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

async function deleteRule(rule) {
  if (!window.confirm(`删除这条规则？\n${targetText(rule)}`)) return
  try {
    await api.deleteVoiceRule(rule.id)
    if (editingId === rule.id) {
      editingId = null
      clearForm()
    }
    await loadRules()
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

/** 只重拉规则表（筛选切换、行内启停用后），其余面板不动。 */
async function loadRules() {
  try {
    rules = await api.voiceRules(filterDevice || undefined)
    renderTable()
    if (editingId !== null) {
      const rule = rules.find((item) => item.id === editingId)
      if (rule) fillForm(rule)
      else exitEdit()
    }
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

async function reload() {
  if (!hasIdentity()) {
    $('voiceStatus').innerHTML = '<div class="empty">语音规则需要管理身份，请在顶栏填写身份标识</div>'
    $('voiceTable').innerHTML = ''
    $('voiceDeliveryTable').innerHTML = ''
    $('voiceEffectTable').innerHTML = '<div class="empty">音效管理需要管理身份</div>'
    $('voiceCount').textContent = ''
    $('voiceDeliveryCount').textContent = ''
    $('voiceEffectCount').textContent = ''
    return
  }
  try {
    const [specResult, ruleList, statusResult, deliveryList, effectList] = await Promise.all([
      api.voiceSpec(),
      api.voiceRules(filterDevice || undefined),
      api.voiceStatus(),
      api.voiceDeliveries(),
      api.voiceEffects(),
      // 第六路是设备清单：进 store（作用域编辑器与 deviceName 都读它），不必接返回值
      refreshDevices(),
    ])
    spec = specResult
    events = specResult.events || []
    placeholders = specResult.placeholders || []
    status = statusResult
    rules = ruleList
    deliveries = deliveryList
    effects = effectList
    renderSpec()
    renderStatus()
    renderFilter()
    renderEffects()
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
          <span class="panel-title">音效库</span>
          <span class="panel-note" id="voiceEffectCount"></span>
        </div>
        <div class="panel-body">
          <div class="field">
            <label for="voiceEffectName">上传音效（mp3/wav/ogg/m4a/aac/flac 等常见格式，按内容校验并统一转换，时长 ≤ 10 秒）</label>
            <div class="btn-row" style="align-items:center">
              <input id="voiceEffectName" type="text" placeholder="名称（如：叮）" style="width:140px">
              <input id="voiceEffectFile" type="file" accept="audio/*">
              <button type="button" class="btn btn-sm" id="btnVoiceEffectUpload">上传</button>
            </div>
            <div class="result" id="voiceEffectResult"></div>
          </div>
          <div id="voiceEffectTable"></div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">语音规则</span>
          <span class="panel-note" id="voiceCount"></span>
          <div class="panel-tools">
            <select id="voiceFilterDevice" style="width:auto"></select>
            <button type="button" class="btn btn-ghost btn-sm" id="btnVoiceReload">刷新</button>
          </div>
        </div>
        <div class="panel-body flush" id="voiceTable"></div>
      </section>

      <section class="panel" id="voiceFormPanel">
        <div class="panel-head">
          <span class="panel-title" id="voiceFormTitle">新增语音规则</span>
        </div>
        <div class="panel-body">
          <div class="field">
            <label>设备</label>
            <div id="voiceScope"></div>
          </div>
          <div class="field">
            <label>学号</label>
            <label class="field-row" style="margin-bottom:4px">
              <input type="checkbox" id="voiceCertWildcard">
              <span>全部学号（不限学号；下方名单变成排除名单）</span>
            </label>
            <textarea id="voiceCert" rows="3" spellcheck="false" placeholder="一行一个学号"></textarea>
            <div class="muted" id="voiceCertHint"></div>
          </div>
          <div class="field">
            <label>事件（可勾多个，每个事件各建一条规则；编辑时只保留一个）</label>
            <div id="voiceEvents"></div>
          </div>
          <div class="field">
            <label for="voiceVoice">音色</label>
            <select id="voiceVoice"></select>
            <div class="muted">每条规则单独选；上方「合成状态」里可逐个试听。</div>
          </div>
          <div class="field">
            <label for="voiceText">播报内容（模板）</label>
            <input id="voiceText" type="text" placeholder="例如：{event}，{door_no} 号喷头">
            <div id="voicePlaceholders"></div>
          </div>
          <div class="field">
            <label for="voiceDelay" id="voiceDelayLabel">触发延迟</label>
            <input id="voiceDelay" type="number" min="0" max="10" step="0.5" value="0">
            <div class="muted">从事件发生时刻算起；浴室 App 自己的语音提示结束后再播，现场实测后调整。窗口内设备会先预下载音频。</div>
          </div>
          <div class="field">
            <label for="voiceVolume" id="voiceVolumeLabel">音量</label>
            <input id="voiceVolume" type="number" min="0" max="5" step="1" value="3">
            <div class="muted">0 为静音；播放结束后恢复设备原音量。</div>
          </div>
          <div class="field">
            <label class="field-row" style="margin:0">
              <input id="voiceEnabled" type="checkbox" checked>
              <span>启用</span>
            </label>
          </div>
          <div class="btn-row">
            <button type="button" class="btn btn-sm" id="btnVoiceSave">新增</button>
            <button type="button" class="btn btn-ghost btn-sm" id="btnVoiceCancel" hidden>取消编辑</button>
            <button type="button" class="btn btn-ghost btn-sm" id="btnVoicePreview">试听示例</button>
          </div>
          <div class="field" id="voiceSample" hidden>
            <label>渲染效果（示例值）</label>
            <div>「<span id="voiceSampleText"></span>」</div>
            <div class="muted">1 号喷头、示例学号等是占位用的示例值；实际播报按事件发生时的真实值渲染。</div>
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
    dispose.add(listen($('btnVoiceEffectUpload'), 'click', uploadEffect))
    dispose.add(
      listen($('voiceEffectFile'), 'change', () => {
        const file = $('voiceEffectFile').files[0]
        // 名称自动取文件名（去扩展名），仍可手改
        if (file) $('voiceEffectName').value = file.name.replace(/\.[^.]+$/, '')
      }),
    )
    dispose.add(
      listen($('voiceEffectTable'), 'click', async (event) => {
        const button = event.target.closest('button[data-act]')
        if (!button) return
        const id = button.closest('tr').dataset.id
        const effect = effects.find((item) => item.effect_id === id)
        if (!effect) return
        if (button.dataset.act === 'audition') {
          stopPreview()
          previewAudio = new Audio(`/voice/effects/${id}.wav`)
          previewAudio.play().catch((error) => toast(`试听失败：${error.message}`))
        }
        if (button.dataset.act === 'delete') await deleteEffect(effect)
        if (button.dataset.act === 'rename') await renameEffect(effect)
      }),
    )
    dispose.add(listen($('btnVoiceSave'), 'click', submitForm))
    dispose.add(listen($('btnVoicePreview'), 'click', previewForm))
    dispose.add(listen($('btnVoiceCancel'), 'click', exitEdit))
    dispose.add(bindScopeToggle(root))
    dispose.add(
      listen($('voicePlaceholders'), 'click', (event) => {
        const chip = event.target.closest('button[data-ph]')
        if (chip) insertPlaceholder(chip.dataset.ph)
      }),
    )
    dispose.add(
      listen($('voiceCertWildcard'), 'change', (event) => {
        // 同一个框换含义：切换时清空，避免目标名单被当成排除名单
        $('voiceCert').value = ''
        updateCertHint()
      }),
    )
    dispose.add(
      listen($('voiceFilterDevice'), 'change', (event) => {
        filterDevice = event.target.value
        loadRules()
      }),
    )
    dispose.add(
      listen($('voiceStatus'), 'click', async (event) => {
        const button = event.target.closest('button[data-act="audition"]')
        if (!button) return
        const voice = button.closest('tr').dataset.voice
        await previewRule({ text: '二五三零一一零零五七 号同学，请到 5 号喷头准备就浴', event: events[0] ? events[0].value : 'assigned', cert_no: '示例学号', voice })
      }),
    )
    dispose.add(
      listen($('voiceTable'), 'click', async (event) => {
        const button = event.target.closest('button[data-act]')
        if (!button) return
        const id = Number(button.closest('tr').dataset.id)
        const rule = rules.find((item) => item.id === id)
        if (!rule) return
        if (button.dataset.act === 'edit') enterEdit(rule)
        if (button.dataset.act === 'toggle') await toggleRule(rule)
        if (button.dataset.act === 'preview') await previewRule(rule)
        if (button.dataset.act === 'push') await pushRule(rule)
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
// marker-test-1792789996
