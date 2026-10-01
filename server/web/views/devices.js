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
import { $, disposer, esc, listen, PLACEHOLDER } from '../core/dom.js'
import { outcomeText, problemText, report } from '../core/outcome.js'
import { refreshDevices, store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

function renderTable() {
  if (store.devices.length === 0) {
    $('deviceTable').innerHTML = '<div class="empty">暂无设备接入</div>'
    return
  }
  const rows = store.devices
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
        <td class="mono">${esc(device.capabilities?.length ?? 0)}</td>
        <td>
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="rename">改名</button>
            <button type="button" class="btn btn-ghost btn-sm" data-act="trust">${device.trusted ? '撤销信任' : '授予信任'}</button>
          </div>
        </td>
      </tr>`,
    )
    .join('')
  $('deviceTable').innerHTML = `<table class="table"><thead><tr>
      <th>设备</th><th>版本</th><th>系统</th><th>网关</th><th>信任</th><th>能力位</th><th>操作</th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

/** 命令与调试通道都只作用于"当前设备"，所以它们要能感知选择变化。 */
function renderCurrent() {
  const device = store.devices.find((item) => item.device_id === store.deviceId)
  $('currentDevice').textContent = device ? `${device.name || device.device_id}（${device.online ? '在线' : '离线'}）` : '未选择'
  const usable = Boolean(device)
  for (const id of ['btnExec', 'btnRestart', 'btnUninstall', 'btnBridgeOpen', 'btnBridgeClose', 'btnBridgeStatus']) {
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
  const device = store.devices.find((item) => item.device_id === deviceId)
  const name = window.prompt('请输入新名称', device?.name || '')
  if (!name) return
  try {
    await api.patchDevice(deviceId, { name })
    await refreshDevices()
    toast('已改名')
  } catch (error) {
    report('maintResult', problemText(error), 'err')
  }
}

async function toggleTrust(deviceId) {
  const device = store.devices.find((item) => item.device_id === deviceId)
  try {
    await api.patchDevice(deviceId, { trusted: !device?.trusted })
    await refreshDevices()
    toast(device?.trusted ? '已取消信任' : '已信任')
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
  if (!window.confirm('卸载设备端程序？设备将断开连接，只能到现场重装。')) return
  report('maintResult', '正在卸载…', 'info')
  try {
    await api.uninstall(store.deviceId)
    report('maintResult', '已完成 · 卸载指令已下发', 'ok')
  } catch (error) {
    report('maintResult', problemText(error), 'err')
  }
}

/**
 * 填出在**你自己电脑上**执行的两条命令，并把它们显示出来。
 *
 * 端口从状态里取真值，不写占位符——那正是这块此前的问题：界面上写的是
 * `adb connect <服务器>:<端口>`，而那是给人看的示意，复制过去跑不起来，得再去翻文档。
 *
 * 第一条用 `-L 端口:127.0.0.1:端口`（两侧同号）：本地也占同一个端口号，与 RUNBOOK
 * 里的写法一致，省得记两个数。
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
 * 查状态、开桥、关桥都会改变桥的状态，而状态行与那两条命令都依赖它。此前开桥与关桥
 * 各自拼一句文案、且都不给命令，于是"开完了怎么连"只能去查文档。
 */
async function refreshBridge() {
  const status = await api.bridgeStatus(store.deviceId)
  if (!status.open) {
    report('bridgeResult', '桥未开启', 'info')
    $('bridgeSteps').hidden = true
    return
  }
  // 只有异常项才值得占位置，正常时保持简短
  const parts = [`世代 ${status.generation}`]
  if (!status.device_connected) parts.push('设备侧未连入')
  if (!status.adb_client_connected) parts.push('本机 adb 未连接')
  parts.push(`${status.expires_in_s} 秒后到期`)
  report('bridgeResult', `桥已开启 · ${parts.join(' · ')}`, 'info')
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
    toast('已开桥')
    await refreshBridge()
  } catch (error) {
    report('bridgeResult', problemText(error), 'err')
  }
}

async function bridgeClose() {
  try {
    await api.bridgeClose(store.deviceId)
    toast('已关桥')
    await refreshBridge()
  } catch (error) {
    report('bridgeResult', problemText(error), 'err')
  }
}

/**
 * 复制一个只读输入框里的内容。
 *
 * `navigator.clipboard` 在非安全上下文或用户拒绝授权时会失败，那时退到"选中让手动复制"
 * ——本条命令很短，手动复制是可行的；直接失败什么都不发生才是让人困惑的。
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
            <div class="field">
              <label for="inCommand">在设备上执行的命令<span class="muted"> · 以 root 身份，回车执行</span></label>
              <div class="field-row">
                <input id="inCommand" class="mono" type="text" autocomplete="off" spellcheck="false"
                       placeholder="例：ls -l /sdcard/ntu-shower">
                <button type="button" class="btn btn-sm" id="btnExec">执行</button>
              </div>
            </div>
            <pre class="code" id="execOutput" hidden></pre>
            <div class="result" id="execResult"></div>

            <!-- 设备自身的动作走另一条通路（即时执行、不进命令通道），结果也另记一处 -->
            <div class="panel-split">
              <div class="panel-split-label">设备自身</div>
              <div class="btn-row">
                <button type="button" class="btn btn-ghost btn-sm" id="btnRestart">重启设备端程序</button>
                <button type="button" class="btn btn-danger btn-sm" id="btnUninstall">卸载设备端程序</button>
              </div>
              <div class="result" id="maintResult"></div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <span class="panel-title">调试通道</span>
            <span class="panel-note">把设备的 ADB 开到你自己电脑上</span>
          </div>
          <div class="panel-body">
            <div class="btn-row">
              <button type="button" class="btn btn-ghost btn-sm" id="btnBridgeStatus">查状态</button>
              <button type="button" class="btn btn-sm" id="btnBridgeOpen">开桥</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnBridgeClose">关桥</button>
            </div>
            <div class="result" id="bridgeResult"></div>

            <div class="muted" id="bridgeHint">
              点「开桥」，然后照这里给出的命令在你自己的电脑上连。连上之后，
              <code class="mono">adb devices</code> 里会多出这台设备。
            </div>

            <div class="field" id="bridgeSteps" hidden>
              <label>在你自己的电脑上执行这两条</label>
              <div class="field-row">
                <input class="mono" id="bridgeTunnelCmd" type="text" readonly>
                <button type="button" class="btn btn-ghost btn-sm" data-copy="bridgeTunnelCmd">复制</button>
              </div>
              <div class="field-row">
                <input class="mono" id="bridgeAdbCmd" type="text" readonly>
                <button type="button" class="btn btn-ghost btn-sm" data-copy="bridgeAdbCmd">复制</button>
              </div>
              <div class="muted">
                第一条保持运行、不要关，它把服务器上的端口转发到你本机；第二条另开一个终端执行。
                连上之后，<code class="mono">adb devices</code> 里会出现这台设备。
              </div>
            </div>
          </div>
        </section>
      </div>`

    dispose = disposer()
    dispose.add(subscribe('devices', renderTable))
    dispose.add(subscribe('device', renderCurrent))
    dispose.add(subscribe('view', (id) => { if (id !== 'devices') dispose.dispose() }))

    dispose.add(listen($('btnReload'), 'click', () => refreshDevices().catch((error) => toast(problemText(error)))))
    dispose.add(listen($('btnExec'), 'click', runExec))
    dispose.add(listen($('btnRestart'), 'click', doRestart))
    dispose.add(listen($('btnUninstall'), 'click', doUninstall))
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
