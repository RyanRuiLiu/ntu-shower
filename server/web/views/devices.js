/**
 * 设备：档案维护 + 远程命令 + 调试通道。
 *
 * 三块放在一个视图里，是因为它们回答的是同一个问题——"这台机器现在怎么样、
 * 我能对它做什么"。档案是身份，命令与调试通道是手段，分开摆会让排障时来回切。
 *
 * ## 危险操作要能被认出来
 *
 * 重启与卸载会停掉设备端的程序，卸载还会摘掉它在服务端的连接。它们在界面上
 * 与普通操作区分开（危险按钮），并且各自要一次确认——误点的代价是跑一趟现场。
 */
import { api } from '../core/api.js'
import { sessionActive } from '../core/api.js'
import { codeCell } from '../core/cells.js'
import { fmtFullDateTime } from '../core/clock.js'
import { REBOOT_CONFIRM } from '../core/copy.js'
import { promptClockSet, restartBathApp } from '../core/device-actions.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { outcomeText, problemText, report } from '../core/outcome.js'
import { refreshDevices, refreshRuntime, store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

function renderTable() {
  if (!sessionActive()) {
    $('deviceTable').innerHTML = '<div class="empty">运维档案需要登录，请在顶栏登录</div>'
    return
  }
  if (store.runtime.length === 0) {
    $('deviceTable').innerHTML = '<div class="empty">暂无设备接入；设备首次连接后在此显示</div>'
    return
  }
  const rows = store.runtime
    .map(
      (device, index) => `
      <tr data-id="${esc(device.device_id)}">
        <td>
          <div>${esc(device.name || PLACEHOLDER)}</div>
          ${codeCell(device.device_id)}
        </td>
        <td class="mono">${esc(device.app_version)} <span class="muted">(${esc(device.version_code)})</span></td>
        <td class="mono">${esc(device.os_version)}</td>
        <td>${device.online ? '<span class="badge badge-green">在线</span>' : '<span class="badge badge-red">离线</span>'}</td>
        <td>${device.trusted ? '<span class="badge badge-green">已信任</span>' : '<span class="badge badge-amber">未信任</span>'}</td>
        <td class="mono" title="${esc((device.capabilities || []).join(', '))}">${esc(
          (device.capabilities || []).length > 0 ? device.capabilities.length : '未声明',
        )}</td>
        <td class="mono">${index + 1}</td>
        <td class="col-actions">
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="up" title="上移" ${index === 0 ? 'disabled' : ''}>↑</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="down" title="下移" ${index === store.runtime.length - 1 ? 'disabled' : ''}>↓</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="rename">重命名</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="trust">${device.trusted ? '撤销信任' : '授予信任'}</button>
          </div>
        </td>
      </tr>`,
    )
    .join('')
  $('deviceTable').innerHTML = `<table class="table"><thead><tr>
      <th>设备</th><th>应用版本</th><th>系统版本</th><th>状态</th><th>信任</th><th>能力位</th><th>顺序</th><th class="col-actions">操作</th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

/**
 * 收起名单编辑区并清空它。
 *
 * 换设备与点「收起」都要这一下：上一台的内容留在框里，下一次按保存就会把那份名单写到
 * 眼前这台设备上。
 */
function closeFreelist() {
  $('freelistEditor').hidden = true
  $('freelistInput').value = ''
  report('freelistResult', '', 'info')
}

/** 只更新“当前设备”标签与可用性；设备清单刷新时用，不能清掉正在看的命令输出。 */
function updateCurrentDevice() {
  const device = store.devices.find((item) => item.device_id === store.deviceId)
  $('currentDevice').textContent = device ? `${device.name || device.device_id}（${device.online ? '在线' : '离线'}）` : '未选择'
  const usable = Boolean(device)
  for (const id of [
    'btnClockSet',
    'btnFreelist',
    'btnRestart',
    'btnBathRestart',
    'btnReboot',
    'btnExec',
    'btnBridgeOpen',
    'btnBridgeClose',
    'btnBridgeStatus',
    'btnLoadLog',
    'btnLoadLogLive',
  ]) {
    $(id).disabled = !usable
  }
}

/** 命令与调试通道都只作用于"当前设备"，切换设备时连同上一台的状态一起收起来。 */
function renderCurrent() {
  updateCurrentDevice()
  $('bridgeSteps').hidden = true
  $('bridgeHint').hidden = false
  $('execOutput').hidden = true
  closeFreelist()
  report('execResult', '', 'info')
  report('maintResult', '', 'info')
  report('bridgeResult', '', 'info')
}

async function rename(deviceId) {
  const device = store.runtime.find((item) => item.device_id === deviceId)
  const name = window.prompt('请输入新名称', device?.name || '')
  if (!name) return
  try {
    await api.patchDevice(deviceId, { name })
    await refreshRuntime()
    toast('已重命名')
  } catch (error) {
    report('maintResult', problemText(error), 'err')
  }
}

async function toggleTrust(deviceId) {
  const device = store.runtime.find((item) => item.device_id === deviceId)
  try {
    await api.patchDevice(deviceId, { trusted: !device?.trusted })
    await refreshRuntime()
    toast(device?.trusted ? '已撤销信任' : '已授予信任')
  } catch (error) {
    report('maintResult', problemText(error), 'err')
  }
}

/**
 * 上移/下移一台设备并保存整个顺序。
 *
 * 顺序只有一份（设备下拉、设备表、统计接口都按它排），所以每次调整都是"本地换位 +
 * 整单保存"：不保存半份，也不做拖拽——楼层的数量级（个位数）配不上那个复杂度。
 */
async function moveDevice(deviceId, direction) {
  const ids = store.runtime.map((item) => item.device_id)
  const from = ids.indexOf(deviceId)
  const to = direction === 'up' ? from - 1 : from + 1
  if (from === -1 || to < 0 || to >= ids.length) return
  ;[ids[from], ids[to]] = [ids[to], ids[from]]
  try {
    await api.saveDeviceOrder(ids)
    await refreshRuntime()
  } catch (error) {
    toast(problemText(error))
  }
}

/** 命令历史（上下键翻）。只存在内存里，刷新页面就清——那正是调试会话的边界。 */
const execHistory = []
let historyIndex = -1

function recallHistory(step) {
  if (execHistory.length === 0) return
  historyIndex = Math.min(Math.max(historyIndex + step, -1), execHistory.length - 1)
  $('inCommand').value = historyIndex < 0 ? '' : execHistory[execHistory.length - 1 - historyIndex]
}

function logKindLabel(kind) {
  if (kind === 'crash') return 'crash.log（未捕获异常）'
  if (kind === 'ota') return 'ota-install.log（安装脚本输出）'
  return 'device.log（运行事件）'
}

function logAbsentText(kind) {
  if (kind === 'crash') return '设备上暂无 crash.log：该设备自上次安装以来没有崩溃过'
  if (kind === 'ota') return '设备上暂无 ota-install.log：还没有发起过安装'
  return '设备上暂无 device.log'
}

function setLogStatus(text) {
  $('logStatus').textContent = text
}

/** 日志是追加写的，默认看最新：换内容后滚到底部。 */
function showLogContent(text) {
  const view = $('deviceLogView')
  view.textContent = text || '(空)'
  requestAnimationFrame(() => {
    view.scrollTop = view.scrollHeight
  })
}

/** 拉取设备最近一次上送的日志（种类取选择框）。404 = 该类日志还没有上送过。 */
async function loadStoredLog() {
  const kind = $('inLogKind').value
  setLogStatus('正在读取上次上送…')
  try {
    const result = await api.deviceLogs(store.deviceId, kind)
    showLogContent(result.content)
    setLogStatus(`上次上送于 ${fmtFullDateTime(result.uploaded_at)}`)
  } catch (error) {
    $('deviceLogView').textContent = ''
    setLogStatus(`${logKindLabel(kind)} 尚无上送记录：${problemText(error)}`)
  }
}

/** 从设备现读最新尾部；离线或命令失败时退回上次上送，但不伪装成实时。 */
async function loadLiveLog() {
  const kind = $('inLogKind').value
  setLogStatus('正在从设备读取最新日志…')
  try {
    const result = await api.deviceLogLive(store.deviceId, kind)
    if (!result.present) {
      $('deviceLogView').textContent = ''
      setLogStatus(logAbsentText(kind))
      return
    }
    showLogContent(result.content)
    const suffix = result.truncated ? ` · 仅显示最近 ${result.lines} 行` : ''
    setLogStatus(`设备实时 · ${fmtFullDateTime(result.read_at)}${suffix}`)
  } catch (error) {
    try {
      const stored = await api.deviceLogs(store.deviceId, kind)
      showLogContent(stored.content)
      setLogStatus(
        `实时读取失败：${problemText(error)}；显示上次上送（${fmtFullDateTime(stored.uploaded_at)}）`,
      )
    } catch (storedError) {
      $('deviceLogView').textContent = ''
      setLogStatus(`实时读取失败：${problemText(error)}；也没有上送记录：${problemText(storedError)}`)
    }
  }
}

async function runExec() {
  const command = $('inCommand').value.trim()
  if (!command) {
    report('execResult', '失败 · 请先填写命令', 'err')
    return
  }
  if (execHistory[execHistory.length - 1] !== command) execHistory.push(command)
  historyIndex = -1
  report('execResult', '正在执行…', 'info')
  $('execOutput').hidden = true
  try {
    const result = await api.exec(store.deviceId, command)
    const output = result.data?.output ?? ''
    $('execOutput').textContent = output || outcomeText(result)
    $('execOutput').hidden = false
    report('execResult', result.status === 'ok' ? '已完成' : outcomeText(result), result.status === 'ok' ? 'ok' : 'err')
  } catch (error) {
    report('execResult', problemText(error), 'err')
  }
}

async function doAppRestart() {
  if (!window.confirm('重启 NTU Shower？期间设备会短暂离线。')) return
  report('maintResult', '正在重启…', 'info')
  try {
    await api.appRestart(store.deviceId)
    report('maintResult', '已完成 · 重启指令已下发', 'ok')
  } catch (error) {
    report('maintResult', problemText(error), 'err')
  }
}

/** 重启整机：设备离线一段时间，重连后由全貌帧重建现态。这是四个里最重的一个，排在最后。 */
async function doReboot() {
  if (!window.confirm(REBOOT_CONFIRM)) return
  report('maintResult', '正在重启设备…', 'info')
  try {
    const result = await api.reboot(store.deviceId)
    report('maintResult', outcomeText(result), result.status === 'ok' ? 'ok' : 'err')
  } catch (error) {
    report('maintResult', problemText(error), 'err')
  }
}

/**
 * 「改设备时间」与「重启浴室 App」的实现在 `core/device-actions.js`——顶栏设备信息面板
 * 的功能行是同一套动作，两处不能各写一份。这里只把结果接到本页的结果区。
 */
function sayMaint(text, kind) {
  report('maintResult', text, kind)
}

/**
 * 展开名单编辑区，并先把设备上那份读进来。
 *
 * 读回来的行直接填进框里：不填的话框是空的，而"看着像是空名单"正是最容易按出误删的
 * 那种误判。
 */
async function openFreelist() {
  $('freelistEditor').hidden = false
  $('freelistInput').value = ''
  report('freelistResult', '正在读取…', 'info')
  try {
    const result = await api.freelist(store.deviceId)
    if (result.status !== 'ok') {
      report('freelistResult', outcomeText(result), 'err')
      return
    }
    $('freelistInput').value = result.students.join('\n')
    report('freelistResult', `已读取 ${result.students.length} 行`, 'info')
  } catch (error) {
    report('freelistResult', problemText(error), 'err')
  }
}

/**
 * 保存名单：整份替换。空框是合法内容（没人免费），所以不拦它。
 *
 * 空行在这里滤掉而不是交给服务端拒：多行文本框里末尾那一下回车不是学号，而人的动作
 * 是"按保存"，不该被 422 挡回来。服务端那一侧的校验照旧——两边各自成立。
 */
async function saveFreelist() {
  const students = $('freelistInput')
    .value.split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '')
  report('freelistResult', '正在保存…', 'info')
  try {
    const result = await api.saveFreelist(store.deviceId, students)
    report('freelistResult', outcomeText(result), result.status === 'ok' ? 'ok' : 'err')
  } catch (error) {
    report('freelistResult', problemText(error), 'err')
  }
}

/**
 * 填出在本机执行的两条连接命令并显示。
 *
 * 端口从状态里取真值，不写占位示意。第一条用 `-L 端口:127.0.0.1:端口`（两侧同号），
 * 与 RUNBOOK 的写法一致，不必记两个数。
 */
function showConnectSteps(status) {
  if (typeof status.port !== 'number') {
    $('bridgeSteps').hidden = true
    $('bridgeHint').hidden = false
    return
  }
  const port = status.port
  $('bridgeTunnelCmd').value = `ssh -N -L ${port}:127.0.0.1:${port} ${status.ssh_target}`
  $('bridgeAdbCmd').value = `adb connect 127.0.0.1:${port}`
  $('bridgeSteps').hidden = false
  $('bridgeHint').hidden = true
}

/**
 * 拉一次桥状态，更新状态行与本机连接命令。三个动作都以它收尾。
 *
 * 查状态、开桥、关桥都会改变桥的状态，而状态行与那两条命令都依赖它。
 */
async function refreshBridge() {
  const status = await api.bridgeStatus(store.deviceId)
  if (!status.open) {
    report('bridgeResult', '通道未开启', 'info')
    $('bridgeSteps').hidden = true
    return
  }
  // 只有异常项才值得占位置，正常时保持简短
  const parts = [`世代 ${status.generation}`]
  if (!status.device_connected) parts.push('设备端未接入')
  if (!status.adb_client_connected) parts.push('本机 ADB 未连接')
  parts.push(`${status.expires_in_s} 秒后到期`)
  report('bridgeResult', `通道已开启 · ${parts.join(' · ')}`, 'info')
  showConnectSteps(status)
}

async function bridgeStatus() {
  try {
    await refreshBridge()
  } catch (error) {
    report('bridgeResult', problemText(error), 'err')
  }
}

async function bridgeOpen() {
  try {
    await api.bridgeOpen(store.deviceId)
    toast('通道已开启')
    await refreshBridge()
  } catch (error) {
    report('bridgeResult', problemText(error), 'err')
  }
}

async function bridgeClose() {
  try {
    await api.bridgeClose(store.deviceId)
    toast('通道已关闭')
    await refreshBridge()
  } catch (error) {
    report('bridgeResult', problemText(error), 'err')
  }
}

/**
 * 复制一个只读输入框里的内容。
 *
 * navigator.clipboard 在非安全上下文或未授权时失败，退到"选中让手动复制"；
 * 直接失败且无提示才让人困惑。
 */
async function copyFrom(id) {
  const input = $(id)
  try {
    await navigator.clipboard.writeText(input.value)
    toast('已复制')
  } catch {
    input.select()
    toast('已选中，按 Ctrl+C 复制')
  }
}

let dispose = disposer()

export const devicesView = {
  id: 'devices',
  title: '设备',

  mount(root) {
    root.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">设备档案</span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnReload">刷新</button>
          </div>
        </div>
        <div class="panel-body flush" id="deviceTable"></div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">设备日志</span>
          <span class="panel-note" id="logStatus">在线可现读最新；离线时看上次上送</span>
          <div class="panel-tools">
            <select id="inLogKind" aria-label="日志种类">
              <option value="device">device.log</option>
              <option value="crash">crash.log</option>
              <option value="ota">ota-install.log</option>
            </select>
            <button type="button" class="btn btn-ghost btn-sm" id="btnLoadLog">上次上送</button>
            <button type="button" class="btn btn-sm" id="btnLoadLogLive">拉取最新</button>
          </div>
        </div>
        <div class="panel-body">
          <pre class="code" id="deviceLogView"></pre>
        </div>
      </section>

      <div class="grid grid-2">
        <section class="panel">
          <div class="panel-head">
            <span class="panel-title">远程命令</span>
            <span class="panel-note">作用于 <b id="currentDevice">未选择</b></span>
          </div>
          <div class="panel-body">
            <!-- 设备自身的动作放在上面：下面那条命令的输出区会随输出长度撑开，
                 排在它后面的东西每次执行都会往下跳，按钮跑掉就不好点了。 -->
            <div class="panel-split-label">设备自身</div>
            <!-- 顺序按影响面从小到大：不动进程的 → 重启本程序 → 重启浴室 APP → 重启整机。
                 破坏性的排最后并用红色，免得误点。 -->
            <div class="btn-row">
              <button type="button" class="btn btn-ghost btn-sm" id="btnClockSet">修改设备端时间</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnFreelist">免费名单</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnRestart">重启 NTU Shower</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnBathRestart">重启浴室 App</button>
              <button type="button" class="btn btn-danger btn-sm" id="btnReboot">重启设备</button>
            </div>
            <div class="result" id="maintResult"></div>

            <!-- 名单编辑区按需展开：那是个多行的框，常年摊在这里会把下面的命令区挤下去。
                 与调试通道那块同一个做法（默认收起，点按钮才出来）。 -->
            <div class="field" id="freelistEditor" hidden>
              <label for="freelistInput">免费名单<span class="muted"> · 一行一个学号，名单里的人不收费</span></label>
              <textarea id="freelistInput" rows="8" spellcheck="false"></textarea>
              <div class="muted">改动在浴室 App 下次启动时生效。</div>
              <div class="btn-row">
                <button type="button" class="btn btn-sm" id="btnFreelistSave">保存</button>
                <button type="button" class="btn btn-ghost btn-sm" id="btnFreelistClose">收起</button>
              </div>
              <div class="result" id="freelistResult"></div>
            </div>

            <div class="panel-split">
              <div class="field">
                <label for="inCommand">在设备上执行的命令<span class="muted"> · 以 root 身份，回车执行</span></label>
                <div class="field-row">
                  <input id="inCommand" class="mono" type="text" autocomplete="off" spellcheck="false">
                  <button type="button" class="btn btn-sm" id="btnExec">执行</button>
                </div>
              </div>
              <pre class="code" id="execOutput" hidden></pre>
              <div class="result" id="execResult"></div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <span class="panel-title">调试通道</span>
            <span class="panel-note">将设备的 ADB 端口转发至本机</span>
          </div>
          <div class="panel-body">
            <div class="btn-row">
              <button type="button" class="btn btn-ghost btn-sm" id="btnBridgeStatus">查询状态</button>
              <button type="button" class="btn btn-sm" id="btnBridgeOpen">开启通道</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnBridgeClose">关闭通道</button>
            </div>
            <div class="result" id="bridgeResult"></div>

            <div class="muted" id="bridgeHint">
              点击「开启通道」，然后在本机执行此处给出的连接命令。连接成功后，
              <code class="mono">adb devices</code> 中将出现该设备。
            </div>

            <div class="field" id="bridgeSteps" hidden>
              <span class="field-label">在本机执行以下两条命令</span>
              <div class="field-row">
                <input class="mono" id="bridgeTunnelCmd" type="text" readonly aria-label="SSH 隧道命令">
                <button type="button" class="btn btn-ghost btn-sm" data-copy="bridgeTunnelCmd"
                  aria-label="复制 SSH 隧道命令">复制</button>
              </div>
              <div class="field-row">
                <input class="mono" id="bridgeAdbCmd" type="text" readonly aria-label="ADB 连接命令">
                <button type="button" class="btn btn-ghost btn-sm" data-copy="bridgeAdbCmd"
                  aria-label="复制 ADB 连接命令">复制</button>
              </div>
              <div class="muted">第一条命令需保持运行，用于将服务器端口转发至本机；第二条请在另一终端中执行。</div>
            </div>
          </div>
        </section>
      </div>`

    dispose = disposer()
    dispose.add(subscribe('runtime', renderTable))
    dispose.add(subscribe('device', renderCurrent))
    // 设备清单刷新时更新标签与按钮；设备已选中时 refreshDevices 不会再发 device，
    // 只靠它会让页面停在初始的“未选择”
    dispose.add(subscribe('devices', updateCurrentDevice))
    dispose.add(subscribe('view', (id) => { if (id !== 'devices') dispose.dispose() }))

    dispose.add(
      listen(root.querySelector('#btnReload'), 'click', () => {
        refreshDevices().catch((error) => toast(problemText(error)))
        refreshRuntime()
          .then((ok) => {
            if (!ok) report('maintResult', '需要管理身份，请在顶栏填写身份标识', 'err')
          })
          .catch((error) => toast(problemText(error)))
      }),
    )
    dispose.add(listen($('btnLoadLog'), 'click', loadStoredLog))
    dispose.add(listen($('btnLoadLogLive'), 'click', loadLiveLog))
    dispose.add(listen($('inLogKind'), 'change', loadStoredLog))
    dispose.add(listen($('btnExec'), 'click', runExec))
    dispose.add(listen($('btnClockSet'), 'click', () => promptClockSet(store.deviceId, sayMaint)))
    dispose.add(listen($('btnFreelist'), 'click', openFreelist))
    dispose.add(listen($('btnFreelistSave'), 'click', saveFreelist))
    dispose.add(listen($('btnFreelistClose'), 'click', closeFreelist))
    dispose.add(listen($('btnRestart'), 'click', doAppRestart))
    dispose.add(listen($('btnBathRestart'), 'click', () => restartBathApp(store.deviceId, sayMaint)))
    dispose.add(listen($('btnReboot'), 'click', doReboot))
    dispose.add(listen($('btnBridgeStatus'), 'click', bridgeStatus))
    dispose.add(listen($('btnBridgeOpen'), 'click', bridgeOpen))
    dispose.add(listen($('btnBridgeClose'), 'click', bridgeClose))

    // 终端里敲命令是按回车，不是去点按钮；上下键翻回上一条也是同一个习惯
    dispose.add(
      listen($('inCommand'), 'keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault()
          runExec()
        } else if (event.key === 'ArrowUp') {
          event.preventDefault()
          recallHistory(1)
        } else if (event.key === 'ArrowDown') {
          event.preventDefault()
          recallHistory(-1)
        }
      }),
    )

    // 命令是只读输入框，用事件委托：两块命令框的复制按钮走同一条路径
    dispose.add(
      listen($('bridgeSteps'), 'click', (event) => {
        const button = event.target.closest('button[data-copy]')
        if (button) copyFrom(button.dataset.copy)
      }),
    )

    dispose.add(
      listen($('deviceTable'), 'click', (event) => {
        const button = event.target.closest('button[data-act]')
        if (!button) return
        const deviceId = button.closest('tr').dataset.id
        if (button.dataset.act === 'rename') rename(deviceId)
        else if (button.dataset.act === 'up' || button.dataset.act === 'down') moveDevice(deviceId, button.dataset.act)
        else toggleTrust(deviceId)
      }),
    )

    renderTable()
    renderCurrent()
  },

  unmount() {
    dispose.dispose()
  },
}
