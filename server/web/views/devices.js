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
import { hasIdentity } from '../core/api.js'
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { outcomeText, problemText, report } from '../core/outcome.js'
import { refreshDevices, refreshRuntime, store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

function renderTable() {
  if (!hasIdentity()) {
    $('deviceTable').innerHTML = '<div class="empty">运维档案需要管理身份，请在顶栏填写身份标识</div>'
    return
  }
  if (store.runtime.length === 0) {
    $('deviceTable').innerHTML = '<div class="empty">暂无设备接入</div>'
    return
  }
  const rows = store.runtime
    .map(
      (device) => `
      <tr data-id="${esc(device.device_id)}">
        <td>
          <div>${esc(device.name || PLACEHOLDER)}</div>
          <code>${esc(device.device_id)}</code>
        </td>
        <td class="mono">${esc(device.app_version)} <span class="muted">(${esc(device.version_code)})</span></td>
        <td class="mono">${esc(device.os_version)}</td>
        <td>${device.online ? '<span class="badge badge-green">在线</span>' : '<span class="badge badge-red">离线</span>'}</td>
        <td>${device.trusted ? '<span class="badge badge-green">已信任</span>' : '<span class="badge badge-amber">未信任</span>'}</td>
        <td class="mono" title="${esc((device.capabilities || []).join(', '))}">${esc(
          (device.capabilities || []).length > 0 ? device.capabilities.length : '未声明',
        )}</td>
        <td>
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="rename">重命名</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="trust">${device.trusted ? '撤销信任' : '授予信任'}</button>
          </div>
        </td>
      </tr>`,
    )
    .join('')
  $('deviceTable').innerHTML = `<table class="table"><thead><tr>
      <th>设备</th><th>应用版本</th><th>系统版本</th><th>状态</th><th>信任</th><th>能力位</th><th>操作</th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

/** 命令与调试通道都只作用于"当前设备"，所以它们要能感知选择变化。 */
function renderCurrent() {
  const device = store.devices.find((item) => item.device_id === store.deviceId)
  $('currentDevice').textContent = device ? `${device.name || device.device_id}（${device.online ? '在线' : '离线'}）` : '未选择'
  const usable = Boolean(device)
  for (const id of ['btnExec', 'btnRestart', 'btnUninstall', 'btnBathRestart', 'btnClockSet', 'btnBridgeOpen', 'btnBridgeClose', 'btnBridgeStatus']) {
    $(id).disabled = !usable
  }
  // 换了设备，上一条连接命令与上一份状态都不再属于眼前这台，收起来免得照着旧端口去连
  $('bridgeSteps').hidden = true
  $('bridgeHint').hidden = false
  $('execOutput').hidden = true
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

/** 命令历史（上下键翻）。只存在内存里，刷新页面就清——那正是调试会话的边界。 */
const execHistory = []
let historyIndex = -1

function recallHistory(step) {
  if (execHistory.length === 0) return
  historyIndex = Math.min(Math.max(historyIndex + step, -1), execHistory.length - 1)
  $('inCommand').value = historyIndex < 0 ? '' : execHistory[execHistory.length - 1 - historyIndex]
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

async function doRestart() {
  if (!window.confirm('重启设备端程序？期间设备会短暂离线。')) return
  report('maintResult', '正在重启…', 'info')
  try {
    await api.restart(store.deviceId)
    report('maintResult', '已完成 · 重启指令已下发', 'ok')
  } catch (error) {
    report('maintResult', problemText(error), 'err')
  }
}

async function doUninstall() {
  if (!window.confirm('卸载设备端程序？卸载后设备将断开连接，需至现场重新安装。')) return
  report('maintResult', '正在卸载…', 'info')
  try {
    await api.uninstall(store.deviceId)
    report('maintResult', '已完成 · 卸载指令已下发', 'ok')
  } catch (error) {
    report('maintResult', problemText(error), 'err')
  }
}

/** 重启浴室 App：强停会中断进行中的会话，必须确认。 */
async function doBathRestart() {
  if (!window.confirm('重启浴室 App？进行中的会话会被中断。')) return
  report('maintResult', '正在重启浴室 App…', 'info')
  try {
    const result = await api.bathRestart(store.deviceId)
    report('maintResult', outcomeText(result), result.status === 'ok' ? 'ok' : 'err')
  } catch (error) {
    report('maintResult', problemText(error), 'err')
  }
}

/**
 * 校正设备系统时间：网页端给定任意时刻，设备端写墙钟。
 *
 * 默认值取本机当前时间（设备与操作者同城，偏差应在校正后的秒级内）；格式校验
 * 在客户端先拦一次，服务端边界仍有同一条正则。
 */
function promptClockSet() {
  const pad = (n) => String(n).padStart(2, '0')
  const now = new Date()
  const defaultVal =
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ` +
    `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
  const input = window.prompt('设置设备系统时间（格式：YYYY-MM-DD HH:MM:SS）', defaultVal)
  if (!input) return
  if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(input.trim())) {
    report('maintResult', '失败 · 时间格式应为 YYYY-MM-DD HH:MM:SS', 'err')
    return
  }
  report('maintResult', '正在设置设备时间…', 'info')
  api.clockSet(store.deviceId, input.trim()).then((result) => {
    report('maintResult', outcomeText(result), result.status === 'ok' ? 'ok' : 'err')
  }).catch((error) => {
    report('maintResult', problemText(error), 'err')
  })
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
            <div class="btn-row">
              <button type="button" class="btn btn-ghost btn-sm" id="btnRestart">重启设备端程序</button>
              <button type="button" class="btn btn-danger btn-sm" id="btnUninstall">卸载设备端程序</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnBathRestart">重启浴室App</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnClockSet">校正时间</button>
            </div>
            <div class="result" id="maintResult"></div>

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
              <label>在本机执行以下两条命令</label>
              <div class="field-row">
                <input class="mono" id="bridgeTunnelCmd" type="text" readonly>
                <button type="button" class="btn btn-ghost btn-sm" data-copy="bridgeTunnelCmd">复制</button>
              </div>
              <div class="field-row">
                <input class="mono" id="bridgeAdbCmd" type="text" readonly>
                <button type="button" class="btn btn-ghost btn-sm" data-copy="bridgeAdbCmd">复制</button>
              </div>
              <div class="muted">
                第一条命令需保持运行，用于将服务器端口转发至本机；第二条请在另一终端中执行。
                连接成功后，<code class="mono">adb devices</code> 中将出现该设备。
              </div>
            </div>
          </div>
        </section>
      </div>`

    dispose = disposer()
    dispose.add(subscribe('runtime', renderTable))
    dispose.add(subscribe('device', renderCurrent))
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
    dispose.add(listen($('btnExec'), 'click', runExec))
    dispose.add(listen($('btnRestart'), 'click', doRestart))
    dispose.add(listen($('btnUninstall'), 'click', doUninstall))
    dispose.add(listen($('btnBathRestart'), 'click', doBathRestart))
    dispose.add(listen($('btnClockSet'), 'click', promptClockSet))
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
