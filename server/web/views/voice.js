/**
 * 语音播报：固定音频素材库 + 自定义分段规则。
 *
 * 规则不再保存文本/音色，而是一串「音频片段 / 喷头号或学号数字 / 停顿」。服务端沿用
 * 「设备 × 学号 × 事件，具体压过通配」匹配，按事件值把分段解析成播放清单；设备按
 * 顺序播放。规则保存后在线设备会预下载用到的素材。
 *
 * 页面只经 core/api.js 出网；表单与表格沿用项目 panel / field / table / badge / btn。
 */
import { api, sessionActive } from '../core/api.js'
import { codeCell, deviceCell } from '../core/cells.js'
import { fmtFullDateTime } from '../core/clock.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { bindScopeToggle, readScopeEditor, scopeEditorHtml } from '../core/device-scope.js'
import { problemText, report, statusBadgeClass, statusLabel } from '../core/outcome.js'
import { deviceName, refreshDevices, store } from '../core/store.js'
import { toast } from '../core/toast.js'

const WILDCARD = '*'
/** 「全部学号」试听时用的示例学号：数字变量需要能念出内容，不能传非数字串。 */
const SAMPLE_CERT_NO = '2026000003'

let spec = null
let events = []
let rules = []
let deliveries = []
let status = null
let assets = []
let editingId = null
let filterDevice = ''
let segments = []
let formReady = false
let previewAudio = null
let previewToken = 0
let dispose = disposer()

function eventItem(value) {
  return events.find((item) => item.value === value) || { value, label: value, description: '' }
}

function assetItem(assetId) {
  return assets.find((item) => item.asset_id === assetId) || null
}

function assetName(assetId) {
  const asset = assetItem(assetId)
  return asset ? asset.name : '缺失素材'
}

function fmtMs(ms) {
  const value = Number(ms || 0)
  return value < 1000 ? `${Math.round(value)} 毫秒` : `${(value / 1000).toFixed(1)} 秒`
}

function fmtSize(bytes) {
  const value = Number(bytes || 0)
  return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KiB` : `${(value / (1024 * 1024)).toFixed(1)} MiB`
}

function sampleCert(certs) {
  return certs.includes(WILDCARD) ? SAMPLE_CERT_NO : certs[0]
}

function segmentsSummary(items) {
  return items
    .map((item) => {
      if (item.kind === 'asset') return assetName(item.asset_id)
      if (item.kind === 'pause') return `停顿 ${fmtMs(item.ms)}`
      const label = item.source === 'door_no' ? '喷头号' : '学号'
      return item.length ? `${label}后 ${item.length} 位` : label
    })
    .join(' · ')
}

/** 数字变量只读数字字符；模板提示里明确这一点。 */
function segmentHint() {
  const hasDoor = segments.some((item) => item.kind === 'digits' && item.source === 'door_no')
  const hasCert = segments.some((item) => item.kind === 'digits' && item.source === 'cert_no')
  if (!hasDoor && !hasCert) return '喷头号按数值念；学号逐位念，结尾字母不发音。'
  const digits = new Set((status?.digits || []).map(Number))
  const missingDigits = [...Array(10).keys()].filter((digit) => !digits.has(digit))
  if (missingDigits.length) return `数字素材缺 ${missingDigits.join('、')}；保存前请先上传。`
  if (hasDoor) {
    const units = new Set(status?.units || [])
    const missingUnits = ['十', '百', '千', '万'].filter((unit) => !units.has(unit))
    if (missingUnits.length) return `单位素材缺 ${missingUnits.join('、')}；喷头号按数值念需要这些单位。`
  }
  return '喷头号按数值念；学号逐位念，结尾字母不发音。'
}

function renderStatus() {
  if (!status) {
    $('voiceStatus').innerHTML = '<div class="empty">还没有读到素材状态</div>'
    return
  }
  const ffmpeg = status.ffmpeg_available
    ? '<span class="badge badge-green">ffmpeg 已就绪</span>'
    : '<span class="badge badge-red">ffmpeg 缺失</span>'
  const digits = status.digits || []
  const digitBadge =
    digits.length === 10
      ? '<span class="badge badge-green">数字素材齐全</span>'
      : `<span class="badge badge-amber">数字素材缺 ${10 - digits.length} 个</span>`
  const units = status.units || []
  const unitBadge =
    units.length === 4
      ? '<span class="badge badge-green">单位素材齐全</span>'
      : `<span class="badge badge-amber">单位素材缺 ${4 - units.length} 个</span>`
  $('voiceStatus').innerHTML = `<div class="btn-row">${ffmpeg}${digitBadge}${unitBadge}<span class="badge badge-blue">素材 ${esc(
    String(status.asset_count),
  )} 个</span></div>`
  $('voiceAssetCount').textContent = status.asset_count > 0 ? `共 ${status.asset_count} 个` : ''
}

function renderDigitOptions() {
  const used = new Set(assets.filter((item) => item.digit !== null).map((item) => Number(item.digit)))
  const options = ['<option value="">不作为数字</option>']
  for (let digit = 0; digit <= 9; digit += 1) {
    const disabled = used.has(digit) ? ' disabled' : ''
    const note = used.has(digit) ? '（已有）' : ''
    options.push(`<option value="${digit}"${disabled}>${digit}${note}</option>`)
  }
  $('voiceAssetDigit').innerHTML = options.join('')
}

function renderUnitOptions() {
  const used = new Set(assets.filter((item) => item.unit).map((item) => item.unit))
  const options = ['<option value="">不作为单位</option>']
  for (const unit of ['十', '百', '千', '万']) {
    const disabled = used.has(unit) ? ' disabled' : ''
    const note = used.has(unit) ? '（已有）' : ''
    options.push(`<option value="${unit}"${disabled}>${unit}${note}</option>`)
  }
  $('voiceAssetUnit').innerHTML = options.join('')
}

function renderAssets() {
  if (assets.length === 0) {
    $('voiceAssetTable').innerHTML = '<div class="empty">还没有素材；先上传一段固定音频。</div>'
    return
  }
  const rows = assets
    .map(
      (asset) => `<tr data-id="${esc(asset.asset_id)}">
        <td>${esc(asset.name)}</td>
        <td>${
          asset.digit !== null
            ? `<span class="badge badge-blue">数字 ${asset.digit}</span>`
            : asset.unit
              ? `<span class="badge badge-purple">单位 ${esc(asset.unit)}</span>`
              : PLACEHOLDER
        }</td>
        <td class="mono">${esc(fmtMs(asset.duration_ms))}</td>
        <td class="mono">${esc(fmtSize(asset.size_bytes))}</td>
        <td class="col-actions">
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="audition">试听</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="rename">改名</button>
            <button type="button" class="btn btn-danger btn-sm" data-act="delete">删除</button>
          </div>
        </td>
      </tr>`,
    )
    .join('')
  $('voiceAssetTable').innerHTML = `<table class="table"><thead><tr>
      <th>名称</th><th>数字 / 单位</th><th>时长</th><th>大小</th><th class="col-actions">操作</th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

function certCell(rule) {
  if (rule.certs.includes(WILDCARD)) {
    const badge = '<span class="badge badge-blue">全部学号</span>'
    if (rule.exempt && rule.exempt.length > 0) {
      return `<div>${badge}</div><div class="muted" title="${esc(rule.exempt.join('、'))}">排除 ${rule.exempt.length} 人</div>`
    }
    return badge
  }
  return rule.certs.map((cert) => codeCell(cert)).join('、')
}

/** 设备清单异步到达后重画作用域；保留用户已选，空选择回到当前设备。 */
function renderScope() {
  const container = $('voiceScope')
  const selected = readScopeEditor(container)
  container.innerHTML = scopeEditorHtml(store.devices, selected === null ? [store.deviceId || WILDCARD] : selected)
}

function renderFilter() {
  const options = [
    ['', '全部规则'],
    [WILDCARD, '仅全部设备（通配）'],
    ...store.devices.map((device) => [device.device_id, deviceName(device.device_id)]),
  ]
  if (!options.some(([value]) => value === filterDevice)) filterDevice = ''
  $('voiceFilterDevice').innerHTML = options
    .map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`)
    .join('')
  $('voiceFilterDevice').value = filterDevice
}

function visibleRules() {
  if (!filterDevice) return rules
  if (filterDevice === WILDCARD) return rules.filter((rule) => rule.devices.includes(WILDCARD))
  return rules.filter((rule) => rule.devices.includes(filterDevice))
}

function renderRules() {
  const list = visibleRules()
  $('voiceCount').textContent = rules.length > 0 ? `共 ${rules.length} 条` : ''
  if (list.length === 0) {
    $('voiceTable').innerHTML = '<div class="empty">暂无匹配的语音规则；调整筛选或新建规则</div>'
    return
  }
  const rows = list
    .map((rule) => {
      const devices = rule.devices.includes(WILDCARD)
        ? '<span class="badge badge-blue">全部设备</span>'
        : rule.devices.map((deviceId) => deviceCell(deviceId)).join('、')
      const eventBadges = rule.events
        .map((value) => {
          const item = eventItem(value)
          return `<span class="badge badge-blue" title="${esc(item.description)}">${esc(item.label)}</span>`
        })
        .join(' ')
      const summary = rule.summary || segmentsSummary(rule.segments)
      const selected = editingId === rule.id ? ' class="is-selected"' : ''
      return `<tr${selected} data-id="${esc(String(rule.id))}">
        <td>${devices}</td>
        <td>${certCell(rule)}</td>
        <td>${eventBadges}<div class="rule-time">时段：${esc(rule.time_summary || '不限')}</div></td>
        <td><div class="muted" title="${esc(summary)}">${esc(summary)}</div></td>
        <td class="mono">音量 ${esc(String(rule.volume))}</td>
        <td>${rule.enabled ? '<span class="badge badge-green">启用</span>' : '<span class="badge">停用</span>'}</td>
        <td class="col-actions">
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="toggle">${rule.enabled ? '停用' : '启用'}</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="edit">编辑</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="preview">预览</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="push">推送</button>
            <button type="button" class="btn btn-danger btn-sm" data-act="delete">删除</button>
          </div>
        </td>
      </tr>`
    })
    .join('')
  $('voiceTable').innerHTML = `<table class="table"><thead><tr>
      <th>设备</th><th>学号</th><th>事件</th><th>分段</th><th>音量</th><th>启用</th><th class="col-actions">操作</th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

function deliveryBadge(value) {
  if (value === 'skipped') return '<span class="badge badge-amber">跳过</span>'
  if (value === 'pending') return '<span class="badge badge-amber">进行中</span>'
  return `<span class="badge ${statusBadgeClass(value)}">${esc(statusLabel(value))}</span>`
}

function renderDeliveries() {
  $('voiceDeliveryCount').textContent = deliveries.length > 0 ? `最近 ${deliveries.length} 条` : ''
  if (deliveries.length === 0) {
    $('voiceDeliveryTable').innerHTML = '<div class="empty">暂无播报记录；触发一次事件或使用规则推送</div>'
    return
  }
  const rows = deliveries
    .map((item) => {
      const event = eventItem(item.event)
      return `<tr>
        <td class="mono">${esc(fmtFullDateTime(item.occurred_at))}</td>
        <td>${item.source === 'manual' ? '<span class="badge badge-blue">手动推送</span>' : '<span class="badge">自动触发</span>'}</td>
        <td>${deviceCell(item.device_id)}</td>
        <td>${codeCell(item.cert_no)}</td>
        <td><span class="badge badge-blue" title="${esc(event.description)}">${esc(event.label)}</span></td>
        <td class="mono">${esc(String(item.door_no))}</td>
        <td><div class="muted" title="${esc(item.segments)}">${esc(item.segments)}</div></td>
        <td class="mono">${esc(fmtMs(item.duration_ms))}</td>
        <td><span title="${esc(item.detail || '')}">${deliveryBadge(item.status)}</span></td>
      </tr>`
    })
    .join('')
  $('voiceDeliveryTable').innerHTML = `<table class="table"><thead><tr>
      <th>下发时刻</th><th>来源</th><th>设备</th><th>学号</th><th>事件</th><th>喷头</th><th>分段</th><th>时长</th><th>状态</th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

function segmentLimitReached() {
  if (!spec || segments.length < spec.max_segments) return false
  report('voiceResult', `失败 · 分段最多 ${spec.max_segments} 个`, 'err')
  return true
}

function renderSegments() {
  if (segments.length === 0) {
    $('voiceSegments').innerHTML = '<div class="empty">还没有分段；用下方按钮添加音频、数字或停顿。</div>'
    $('voiceSegmentHint').textContent = segmentHint()
    return
  }
  const rows = segments
    .map((segment, index) => {
      const controls = []
      if (segment.kind === 'asset') {
        const options = assets
          .map(
            (asset) =>
              `<option value="${esc(asset.asset_id)}"${asset.asset_id === segment.asset_id ? ' selected' : ''}>${esc(
                asset.name,
              )}</option>`,
          )
          .join('')
        controls.push(`<span class="seg-kind">音频</span>`)
        controls.push(
          `<select data-seg-field="asset" data-index="${index}"
            aria-label="第 ${index + 1} 段的音频素材">${options}</select>`,
        )
      } else if (segment.kind === 'digits') {
        controls.push(`<span class="seg-kind">数字</span>`)
        controls.push(
          `<select data-seg-field="source" data-index="${index}"
            aria-label="第 ${index + 1} 段的数字来源">
            <option value="door_no"${segment.source === 'door_no' ? ' selected' : ''}>喷头号（按数值）</option>
            <option value="cert_no"${segment.source === 'cert_no' ? ' selected' : ''}>学号（逐位）</option>
          </select>`,
        )
        if (segment.source === 'cert_no') {
          controls.push(
            `<input type="number" min="1" max="10" data-seg-field="length" data-index="${index}"
              value="${segment.length || ''}" placeholder="全部位数"
              aria-label="第 ${index + 1} 段取学号的位数（留空为全部位数）">`,
          )
        } else {
          controls.push('<span class="muted">按数值</span>')
        }
      } else {
        controls.push(`<span class="seg-kind">停顿</span>`)
        controls.push(
          `<input type="number" min="100" max="5000" step="100" data-seg-field="pause" data-index="${index}"
            value="${segment.ms}" aria-label="第 ${index + 1} 段停顿时长（毫秒）">`,
        )
        controls.push('<span class="muted">毫秒</span>')
      }
      controls.push(`<div class="btn-row">
        <button type="button" class="btn btn-ghost btn-sm" data-seg-act="up" data-index="${index}"
          aria-label="上移第 ${index + 1} 段" ${index === 0 ? 'disabled' : ''}>↑</button>
        <button type="button" class="btn btn-ghost btn-sm" data-seg-act="down" data-index="${index}"
          aria-label="下移第 ${index + 1} 段" ${index === segments.length - 1 ? 'disabled' : ''}>↓</button>
        <button type="button" class="btn btn-danger btn-sm" data-seg-act="remove" data-index="${index}"
          aria-label="移除第 ${index + 1} 段">移除</button>
      </div>`)
      return `<div class="seg-row">${controls.join('')}</div>`
    })
    .join('')
  $('voiceSegments').innerHTML = rows
  $('voiceSegmentHint').textContent = segmentHint()
}

function renderSpec() {
  const volume = spec ? spec.volume : { minimum: 0, maximum: 5, default: 3 }
  $('voiceVolumeLabel').textContent = `音量（${volume.minimum}–${volume.maximum}）`
  $('voiceVolume').min = String(volume.minimum)
  $('voiceVolume').max = String(volume.maximum)
  if (!spec) return
  $('voiceVolume').value = String(volume.default)
}

function eventChecksHtml(selected) {
  const phases = [...new Set(events.map((item) => item.phase || '其它'))]
  return phases
    .map((phase) => {
      const boxes = events
        .filter((item) => (item.phase || '其它') === phase)
        .map(
          (item) => `<label class="field-row">
              <input type="checkbox" data-event="${esc(item.value)}" ${selected.includes(item.value) ? 'checked' : ''}>
              <span title="${esc(item.description)}">${esc(item.label)}</span>
            </label>`,
        )
        .join('')
      return `<div class="check-group" role="group" aria-label="${esc(phase)}">
        <span class="muted">${esc(phase)}</span><div class="btn-row">${boxes}</div></div>`
    })
    .join('')
}

function renderEventChoices(selected) {
  $('voiceEvents').innerHTML = eventChecksHtml(selected)
}

function readEventSelection() {
  return [...document.querySelectorAll('#voiceEvents input[data-event]:checked')].map((input) => input.dataset.event)
}

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

function timeWindowRowHtml(value, index) {
  const selected = value?.days?.length ? value.days : [1, 2, 3, 4, 5, 6, 7]
  const days = WEEKDAYS.map(
    (label, dayIndex) => `<label class="weekday-option">
        <input type="checkbox" data-window-day="${dayIndex + 1}" ${selected.includes(dayIndex + 1) ? 'checked' : ''}>
        <span>${label}</span>
      </label>`,
  ).join('')
  return `<div class="time-window" data-window>
      <div class="weekday-options" role="group" aria-label="第 ${index + 1} 个时段的星期">${days}</div>
      <div class="btn-row">
        <input type="time" data-window-start value="${esc(value?.start || '')}"
          aria-label="第 ${index + 1} 个时段的开始时刻">
        <span class="muted">至</span>
        <input type="time" data-window-end value="${esc(value?.end || '')}"
          aria-label="第 ${index + 1} 个时段的结束时刻">
        <button type="button" class="btn btn-danger btn-sm" data-remove-window
          aria-label="移除第 ${index + 1} 个时段">移除</button>
      </div>
    </div>`
}

function renderTimeWindows(windows) {
  $('voiceTimeWindows').innerHTML = windows.map((window, index) => timeWindowRowHtml(window, index)).join('')
}

function updateTimeWindowState() {
  const unlimited = $('voiceTimeUnlimited').checked
  $('voiceTimeWindows').hidden = unlimited
  $('voiceTimeAddWrap').hidden = unlimited
  if (!unlimited && $('voiceTimeWindows').children.length === 0) addTimeWindowRow()
}

function addTimeWindowRow(window = null) {
  if (!spec) {
    report('voiceResult', '失败 · 规则规格尚未加载', 'err')
    return
  }
  if ($('voiceTimeWindows').children.length >= spec.max_time_windows) {
    report('voiceResult', `失败 · 最多 ${spec.max_time_windows} 个时段`, 'err')
    return
  }
  $('voiceTimeWindows').insertAdjacentHTML(
    'beforeend',
    timeWindowRowHtml(window, $('voiceTimeWindows').children.length),
  )
}

function readTimeWindows() {
  if ($('voiceTimeUnlimited').checked) return []
  const rows = [...$('voiceTimeWindows').querySelectorAll('[data-window]')]
  if (rows.length === 0) throw new Error('请至少添加一个时段，或勾选不限时段')
  return rows.map((row) => {
    const days = [...row.querySelectorAll('input[data-window-day]:checked')].map((input) =>
      Number(input.dataset.windowDay),
    )
    const start = row.querySelector('[data-window-start]').value
    const end = row.querySelector('[data-window-end]').value
    if (days.length === 0) throw new Error('每个时段至少选择一天')
    if (!start || !end) throw new Error('每个时段都要填写开始和结束时刻')
    if (start === end) throw new Error('时段的开始和结束时刻不能相同')
    return { days, start, end }
  })
}

function applyFormMode() {
  const editing = editingId !== null
  $('voiceFormTitle').textContent = editing ? '编辑语音规则' : '新增语音规则'
  $('btnVoiceSave').textContent = editing ? '保存修改' : '新增'
  $('btnVoiceCancel').hidden = !editing
}

function clearForm() {
  editingId = null
  $('voiceScope').innerHTML = scopeEditorHtml(store.devices, [store.deviceId || WILDCARD])
  $('voiceCertWildcard').checked = false
  $('voiceCert').disabled = false
  $('voiceCert').value = ''
  updateCertHint()
  renderEventChoices([])
  $('voiceTimeUnlimited').checked = true
  renderTimeWindows([])
  updateTimeWindowState()
  segments = []
  renderSegments()
  renderSpec()
  $('voiceEnabled').checked = true
  $('voiceResult').textContent = ''
  applyFormMode()
}

function fillForm(rule) {
  editingId = rule.id
  $('voiceScope').innerHTML = scopeEditorHtml(store.devices, rule.devices)
  const wildcard = rule.certs.includes(WILDCARD)
  $('voiceCertWildcard').checked = wildcard
  $('voiceCert').disabled = false
  $('voiceCert').value = wildcard ? (rule.exempt || []).join('\n') : rule.certs.join('\n')
  updateCertHint()
  renderEventChoices(rule.events)
  $('voiceTimeUnlimited').checked = rule.time_windows.length === 0
  renderTimeWindows(rule.time_windows)
  updateTimeWindowState()
  segments = rule.segments.map((item) => ({ ...item }))
  renderSegments()
  $('voiceVolume').value = String(rule.volume)
  $('voiceEnabled').checked = rule.enabled
  applyFormMode()
  $('voiceFormPanel').scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function exitEdit() {
  editingId = null
  clearForm()
  renderRules()
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

function updateCertHint() {
  $('voiceCertHint').textContent = $('voiceCertWildcard').checked
    ? '排除名单：一行一个学号，这些人不播这条规则（可能命中其它规则）。'
    : '一行一个学号；这些学号都会播这条规则。'
}

function readTargets() {
  if ($('voiceCertWildcard').checked) return { certs: [WILDCARD], exempt: readCertLines() }
  return { certs: readCertLines(), exempt: [] }
}

function readForm() {
  const deviceIds = readScopeEditor($('voiceScope'))
  if (deviceIds === null) {
    report('voiceResult', '失败 · 请选择设备（或勾选全部设备）', 'err')
    return null
  }
  const { certs, exempt } = readTargets()
  if (certs.length === 0) {
    report('voiceResult', '失败 · 请填写学号（或勾选全部学号）', 'err')
    return null
  }
  const selectedEvents = readEventSelection()
  if (selectedEvents.length === 0) {
    report('voiceResult', '失败 · 请勾选至少一个事件', 'err')
    return null
  }
  let timeWindows
  try {
    timeWindows = readTimeWindows()
  } catch (error) {
    report('voiceResult', `失败 · ${error.message}`, 'err')
    return null
  }
  if (segments.length === 0) {
    report('voiceResult', '失败 · 请至少添加一个分段', 'err')
    return null
  }
  return {
    devices: deviceIds,
    certs,
    events: selectedEvents,
    time_windows: timeWindows,
    segments: segments.map((item) => ({ ...item })),
    volume: Number($('voiceVolume').value),
    enabled: $('voiceEnabled').checked,
    exempt_cert_nos: exempt,
  }
}

function stopPreview() {
  previewToken += 1
  if (previewAudio) {
    previewAudio.pause()
    previewAudio = null
  }
}

function playUrl(url, token) {
  return new Promise((resolve) => {
    if (!url || token !== previewToken) return resolve()
    const audio = new Audio(url)
    previewAudio = audio
    audio.onended = () => resolve()
    audio.onerror = () => {
      toast('试听失败')
      resolve()
    }
    audio.play().catch(() => resolve())
  })
}

function pause(ms, token) {
  return new Promise((resolve) => {
    window.setTimeout(() => resolve(token !== previewToken), ms)
  })
}

async function playResolved(list) {
  stopPreview()
  const token = ++previewToken
  for (const segment of list) {
    if (token !== previewToken) return
    if (segment.kind === 'pause') {
      await pause(segment.ms, token)
    } else if (segment.clip) {
      await playUrl(segment.clip.url, token)
    }
  }
}

async function previewSegments(items, event, cert, doorNo = 1) {
  if (!items || items.length === 0) {
    report('voiceResult', '失败 · 请至少添加一个分段', 'err')
    return
  }
  try {
    const result = await api.previewVoiceRule({ segments: items, event, cert_no: cert, door_no: doorNo })
    report('voiceResult', `预览：${result.summary}（共 ${fmtMs(result.total_ms)}）`, 'info')
    await playResolved(result.segments)
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

async function previewForm() {
  const payload = readForm()
  if (!payload) return
  await previewSegments(payload.segments, payload.events[0], sampleCert(payload.certs))
}

async function previewRule(rule) {
  await previewSegments(rule.segments, rule.events[0], sampleCert(rule.certs))
}

async function submitForm() {
  const payload = readForm()
  if (!payload) return
  report('voiceResult', '正在保存…', 'info')
  const editing = editingId !== null
  try {
    if (editing) {
      await api.updateVoiceRule(editingId, payload)
    } else {
      await api.createVoiceRule(payload)
    }
    clearForm()
    await reload()
    report(
      'voiceResult',
      editing ? '已保存；在线目标设备已排入素材预下载。' : '已新建；在线目标设备已排入素材预下载。',
      'ok',
    )
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

async function toggleRule(rule) {
  try {
    await api.patchVoiceRule(rule.id, { enabled: !rule.enabled })
    await reload()
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

async function deleteRule(rule) {
  if (!window.confirm(`删除这条规则？\n${rule.summary}`)) return
  try {
    await api.deleteVoiceRule(rule.id)
    if (editingId === rule.id) exitEdit()
    await reload()
    toast('已删除')
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

async function pushRule(rule) {
  const allowed = rule.devices.includes(WILDCARD)
    ? store.devices
    : store.devices.filter((device) => rule.devices.includes(device.device_id))
  const online = allowed.filter((device) => device.online)
  if (online.length === 0) {
    report('voiceResult', '失败 · 没有在线设备可推送', 'err')
    return
  }
  let deviceId = online.length === 1 ? online[0].device_id : ''
  if (!deviceId) {
    const list = online.map((device, index) => `${index + 1}. ${device.name || device.device_id}`).join('\n')
    const picked = window.prompt(`这条规则作用于多台设备。推送到哪台？输入序号：\n${list}`, '1')
    if (picked === null) return
    const chosen = online[Number(picked) - 1]
    if (!chosen) {
      report('voiceResult', '失败 · 序号无效', 'err')
      return
    }
    deviceId = chosen.device_id
  }
  report('voiceResult', `正在推送到 ${deviceName(deviceId)} 播放…`, 'info')
  try {
    const result = await api.pushVoiceRule(rule.id, deviceId)
    const ok = result.status === 'ok'
    report(
      'voiceResult',
      ok ? `已在 ${deviceName(deviceId)} 上播放（${result.segment_count} 段）。` : `设备返回：${result.detail || result.status}`,
      ok ? 'ok' : 'err',
    )
    await reload()
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

function assetResult(message, level = 'info') {
  report('voiceAssetResult', message, level)
}

async function uploadAsset() {
  const name = $('voiceAssetName').value.trim()
  const file = $('voiceAssetFile').files[0]
  if (!name) return assetResult('失败 · 请填写素材名称', 'err')
  if (!file) return assetResult('失败 · 请选择音频文件', 'err')
  const form = new FormData()
  form.append('name', name)
  form.append('file', file)
  form.append('digit', $('voiceAssetDigit').value)
  form.append('unit', $('voiceAssetUnit').value)
  assetResult('正在上传并转换…')
  try {
    await api.uploadVoiceAsset(form)
    $('voiceAssetName').value = ''
    $('voiceAssetFile').value = ''
    $('voiceAssetDigit').value = ''
    $('voiceAssetUnit').value = ''
    await reload()
    assetResult('素材已入库；创建规则后在线设备会预下载。', 'ok')
  } catch (error) {
    assetResult(problemText(error), 'err')
  }
}

async function renameAsset(asset) {
  const name = window.prompt('新的素材名称', asset.name)
  if (name === null) return
  const trimmed = name.trim()
  if (!trimmed || trimmed === asset.name) return
  try {
    await api.renameVoiceAsset(asset.asset_id, trimmed)
    await reload()
    toast('已改名')
  } catch (error) {
    assetResult(problemText(error), 'err')
  }
}

async function deleteAsset(asset) {
  if (!window.confirm(`删除素材「${asset.name}」？`)) return
  try {
    await api.deleteVoiceAsset(asset.asset_id)
    segments = segments.filter((item) => item.kind !== 'asset' || item.asset_id !== asset.asset_id)
    await reload()
    toast('已删除')
  } catch (error) {
    assetResult(problemText(error), 'err')
  }
}

async function reload() {
  if (!sessionActive()) {
    $('voiceStatus').innerHTML = '<div class="empty">语音素材与规则需要登录，请在顶栏登录</div>'
    $('voiceAssetTable').innerHTML = ''
    $('voiceTable').innerHTML = ''
    $('voiceDeliveryTable').innerHTML = ''
    $('voiceAssetCount').textContent = ''
    $('voiceDeliveryCount').textContent = ''
    $('voiceCount').textContent = ''
    return
  }
  try {
    const [specResult, statusResult, assetList, ruleList, deliveryList] = await Promise.all([
      api.voiceSpec(),
      api.voiceStatus(),
      api.voiceAssets(),
      api.voiceRules(),
      api.voiceDeliveries(),
      refreshDevices(),
    ])
    spec = specResult
    events = specResult.events || []
    status = statusResult
    assets = assetList
    rules = ruleList
    deliveries = deliveryList
    renderStatus()
    renderDigitOptions()
    renderUnitOptions()
    renderAssets()
    renderFilter()
    renderRules()
    renderDeliveries()
    if (!formReady) {
      formReady = true
      clearForm()
    } else {
      renderScope()
      renderEventChoices(readEventSelection())
    }
    renderSegments()
    if (editingId !== null && !rules.some((item) => item.id === editingId)) exitEdit()
  } catch (error) {
    report('voiceResult', problemText(error), 'err')
  }
}

export const voiceView = {
  id: 'voice',
  title: '语音播报',

  mount(root) {
    root.innerHTML = `
      <div class="grid grid-2 voice-top">
        <section class="panel">
          <div class="panel-head">
            <span class="panel-title">音频素材</span>
            <span class="panel-note" id="voiceAssetCount"></span>
            <div class="panel-tools">
              <button type="button" class="btn btn-ghost btn-sm" id="btnVoiceReload">刷新</button>
            </div>
          </div>
          <div class="panel-body flush voice-asset-table" id="voiceAssetTable"></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <span class="panel-title">上传素材</span>
          </div>
          <div class="panel-body">
            <div class="btn-row" id="voiceStatus"></div>
            <form id="voiceAssetForm" class="field">
              <label for="voiceAssetName">上传固定音频（MP3/WAV/OGG/M4A 等，服务端统一转 MP3）</label>
              <input id="voiceAssetName" type="text" maxlength="40" placeholder="素材名称，例如：请使用">
              <div class="btn-row">
                <input id="voiceAssetFile" type="file" accept="audio/*" aria-label="音频文件">
                <select id="voiceAssetDigit" aria-label="数字素材"></select>
                <select id="voiceAssetUnit" aria-label="单位素材"></select>
                <button type="submit" class="btn btn-sm" id="btnVoiceAssetUpload">上传</button>
              </div>
              <div class="hint">数字素材用于规则里的「喷头号/学号」；0–9 各一段。上传后可改名，被规则引用时不能删除。</div>
            </form>
            <div class="result" id="voiceAssetResult"></div>
          </div>
        </section>
      </div>

      <div class="grid-list-form">
        <section class="panel">
          <div class="panel-head">
            <span class="panel-title">语音规则</span>
            <span class="panel-note" id="voiceCount"></span>
            <div class="panel-tools">
              <select id="voiceFilterDevice" aria-label="按设备筛选语音规则"></select>
              <button type="button" class="btn btn-ghost btn-sm" id="btnVoiceReloadRules">刷新</button>
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
              <span class="field-label" id="voiceScopeLabel">设备</span>
              <div id="voiceScope" role="group" aria-labelledby="voiceScopeLabel"></div>
            </div>
            <div class="field">
              <label for="voiceCert">学号</label>
              <label class="field-row">
                <input id="voiceCertWildcard" type="checkbox">
                <span>全部学号（不限学号；下方名单变成排除名单）</span>
              </label>
              <textarea id="voiceCert" rows="3" placeholder="一行一个学号"></textarea>
              <div class="hint" id="voiceCertHint">一行一个学号；这些学号都会播这条规则。</div>
            </div>
            <div class="field">
              <span class="field-label" id="voiceEventsLabel">事件（可勾多个）</span>
              <div id="voiceEvents" role="group" aria-labelledby="voiceEventsLabel"></div>
            </div>
            <div class="field" role="group" aria-labelledby="voiceTimeLabel">
              <span class="field-label" id="voiceTimeLabel">生效时段</span>
              <label class="field-row">
                <input id="voiceTimeUnlimited" type="checkbox" checked>
                <span>不限时段</span>
              </label>
              <div class="time-windows" id="voiceTimeWindows"></div>
              <div class="btn-row" id="voiceTimeAddWrap" hidden>
                <button type="button" class="btn btn-ghost btn-sm" id="btnVoiceAddWindow">添加时段</button>
              </div>
              <div class="hint">跨天按开始日算；结束时刻不含。按服务端当地时间判断。</div>
            </div>
            <div class="hint">匹配优先级：设备具体 → 学号具体 → 同档集合更小 → 时段更紧。完全同优先级且目标与时段都重叠的规则不能保存。</div>
            <div class="field">
              <span class="field-label" id="voiceSegmentsLabel">播报分段</span>
              <div class="seg-editor" id="voiceSegments" role="group" aria-labelledby="voiceSegmentsLabel"></div>
              <div class="btn-row">
                <button type="button" class="btn btn-ghost btn-sm" id="btnAddAsset">加音频</button>
                <button type="button" class="btn btn-ghost btn-sm" id="btnAddDigits">加数字</button>
                <button type="button" class="btn btn-ghost btn-sm" id="btnAddPause">加停顿</button>
              </div>
              <div class="hint" id="voiceSegmentHint"></div>
            </div>
            <div class="field">
              <label for="voiceVolume" id="voiceVolumeLabel">音量</label>
              <input id="voiceVolume" type="number" min="0" max="5" step="1" value="3">
              <div class="muted">0 为静音；播放结束后恢复设备原音量。</div>
            </div>
            <div class="field">
              <label class="field-row">
                <input id="voiceEnabled" type="checkbox" checked>
                <span>启用</span>
              </label>
            </div>
            <div class="btn-row">
              <button type="button" class="btn btn-sm" id="btnVoiceSave">新增</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnVoiceCancel" hidden>取消编辑</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnVoicePreview">预览</button>
            </div>
            <div class="result" id="voiceResult"></div>
          </div>
        </section>
      </div>

      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">最近推送</span>
          <span class="panel-note" id="voiceDeliveryCount"></span>
        </div>
        <div class="panel-body flush" id="voiceDeliveryTable"></div>
      </section>`

    dispose = disposer()
    formReady = false
    dispose.add(bindScopeToggle(root))
    dispose.add(listen($('btnVoiceReload'), 'click', reload))
    dispose.add(listen($('btnVoiceReloadRules'), 'click', reload))
    dispose.add(listen($('voiceFilterDevice'), 'change', (event) => {
      filterDevice = event.target.value
      renderRules()
    }))
    dispose.add(listen($('voiceAssetForm'), 'submit', (event) => {
      event.preventDefault()
      uploadAsset()
    }))
    dispose.add(listen($('voiceAssetFile'), 'change', () => {
      const file = $('voiceAssetFile').files[0]
      if (file && !$('voiceAssetName').value.trim()) {
        $('voiceAssetName').value = file.name.replace(/\.[^.]+$/, '')
      }
    }))
    dispose.add(
      listen($('voiceAssetDigit'), 'change', () => {
        if ($('voiceAssetDigit').value) $('voiceAssetUnit').value = ''
      }),
    )
    dispose.add(
      listen($('voiceAssetUnit'), 'change', () => {
        if ($('voiceAssetUnit').value) $('voiceAssetDigit').value = ''
      }),
    )
    dispose.add(listen($('voiceTimeUnlimited'), 'change', updateTimeWindowState))
    dispose.add(listen($('btnVoiceAddWindow'), 'click', () => addTimeWindowRow()))
    dispose.add(
      listen($('voiceTimeWindows'), 'click', (event) => {
        const button = event.target.closest('[data-remove-window]')
        if (!button) return
        button.closest('[data-window]')?.remove()
        if ($('voiceTimeWindows').children.length === 0 && !$('voiceTimeUnlimited').checked) addTimeWindowRow()
      }),
    )
    dispose.add(listen($('voiceAssetTable'), 'click', (event) => {
      const button = event.target.closest('button[data-act]')
      if (!button) return
      const asset = assetItem(button.closest('[data-id]')?.dataset.id)
      if (!asset) return
      if (button.dataset.act === 'audition') {
        stopPreview()
        const audio = new Audio(asset.url)
        previewAudio = audio
        audio.play().catch(() => toast('试听失败'))
      }
      if (button.dataset.act === 'rename') renameAsset(asset)
      if (button.dataset.act === 'delete') deleteAsset(asset)
    }))
    dispose.add(listen($('voiceSegments'), 'click', (event) => {
      const button = event.target.closest('button[data-seg-act]')
      if (!button) return
      const index = Number(button.dataset.index)
      const action = button.dataset.segAct
      if (action === 'up' && index > 0) {
        ;[segments[index - 1], segments[index]] = [segments[index], segments[index - 1]]
      } else if (action === 'down' && index < segments.length - 1) {
        ;[segments[index + 1], segments[index]] = [segments[index], segments[index + 1]]
      } else if (action === 'remove') {
        segments.splice(index, 1)
      }
      renderSegments()
    }))
    dispose.add(listen($('voiceSegments'), 'change', (event) => {
      const field = event.target.dataset.segField
      if (!field) return
      const index = Number(event.target.dataset.index)
      const segment = segments[index]
      if (!segment) return
      if (field === 'asset') segment.asset_id = event.target.value
      if (field === 'source') {
        segment.source = event.target.value
        segment.length = null
        renderSegments()
        return
      }
      if (field === 'length') segment.length = event.target.value ? Number(event.target.value) : null
      if (field === 'pause') segment.ms = Number(event.target.value)
      $('voiceSegmentHint').textContent = segmentHint()
    }))
    dispose.add(listen($('btnAddAsset'), 'click', () => {
      if (segmentLimitReached()) return
      if (assets.length === 0) {
        assetResult('失败 · 先在「音频素材」里上传音频', 'err')
        return
      }
      segments.push({ kind: 'asset', asset_id: assets[0].asset_id })
      renderSegments()
    }))
    dispose.add(listen($('btnAddDigits'), 'click', () => {
      if (segmentLimitReached()) return
      segments.push({ kind: 'digits', source: 'door_no', length: null })
      renderSegments()
    }))
    dispose.add(listen($('btnAddPause'), 'click', () => {
      if (segmentLimitReached()) return
      segments.push({ kind: 'pause', ms: 500 })
      renderSegments()
    }))
    dispose.add(listen($('voiceTable'), 'click', async (event) => {
      const button = event.target.closest('button[data-act]')
      if (!button) return
      const id = Number(button.closest('[data-id]')?.dataset.id)
      const rule = rules.find((item) => item.id === id)
      if (!rule) return
      if (button.dataset.act === 'edit') fillForm(rule)
      if (button.dataset.act === 'toggle') await toggleRule(rule)
      if (button.dataset.act === 'preview') await previewRule(rule)
      if (button.dataset.act === 'push') await pushRule(rule)
      if (button.dataset.act === 'delete') await deleteRule(rule)
    }))
    dispose.add(listen($('voiceCertWildcard'), 'change', () => {
      $('voiceCert').value = ''
      updateCertHint()
    }))
    dispose.add(listen($('btnVoiceSave'), 'click', submitForm))
    dispose.add(listen($('btnVoicePreview'), 'click', previewForm))
    dispose.add(listen($('btnVoiceCancel'), 'click', exitEdit))
    exitEdit()
    reload()
  },

  unmount() {
    stopPreview()
    dispose.dispose()
  },
}
