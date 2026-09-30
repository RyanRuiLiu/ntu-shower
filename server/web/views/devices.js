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

async function runExec() {
  const command = $('inCommand').value.trim()
  if (!command) {
    report('maintResult', '失败 · 请先填写命令', 'err')
    return
  }
  report('maintResult', '正在执行…', 'info')
  $('execOutput').hidden = true
  try {
    const result = await api.exec(store.deviceId, command)
    const output = result.data?.output ?? ''
    $('execOutput').textContent = output || outcomeText(result)
    $('execOutput').hidden = false
    report('maintResult', result.status === 'ok' ? '已完成' : outcomeText(result), result.status === 'ok' ? 'ok' : 'err')
  } catch (error) {
    report('maintResult', problemText(error), 'err')
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

async function bridgeStatus() {
  try {
    const status = await api.bridgeStatus(store.deviceId)
    // 先判断 open：桥关闭时契约里只有 device_id 与 open，其余字段根本不存在，
    // 直接读会得到一排 undefined。只有异常项才值得占位置，正常时保持简短。
    if (!status.open) {
      report('bridgeResult', '桥未开启', 'info')
      return
    }
    const parts = [`本机 127.0.0.1:${status.port}`, `世代 ${status.generation}`]
    if (!status.device_connected) parts.push('设备侧未连入')
    if (!status.adb_client_connected) parts.push('本机 adb 未连接')
    parts.push(`${status.expires_in_s} 秒后到期`)
    report('bridgeResult', `桥已开启 · ${parts.join(' · ')}`, 'info')
  } catch (error) {
    report('bridgeResult', problemText(error), 'err')
  }
}

async function bridgeOpen() {
  try {
    const result = await api.bridgeOpen(store.deviceId)
    report('bridgeResult', `已完成 · 桥已开：本机 127.0.0.1:${result.port}（世代 ${result.generation}）`, 'ok')
  } catch (error) {
    report('bridgeResult', problemText(error), 'err')
  }
}

async function bridgeClose() {
  try {
    await api.bridgeClose(store.deviceId)
    report('bridgeResult', '已完成 · 桥已关闭', 'ok')
  } catch (error) {
    report('bridgeResult', problemText(error), 'err')
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
              <label for="inCommand">root shell 命令</label>
              <div class="field-row">
                <input id="inCommand" type="text" placeholder="例：ls -l /sdcard/ntu-shower">
                <button type="button" class="btn btn-sm" id="btnExec">执行</button>
              </div>
            </div>
            <pre class="code" id="execOutput" hidden></pre>
            <div class="result" id="maintResult"></div>

            <div class="btn-row" style="margin-top:12px">
              <button type="button" class="btn btn-ghost btn-sm" id="btnRestart">重启设备端程序</button>
              <button type="button" class="btn btn-danger btn-sm" id="btnUninstall">卸载设备端程序</button>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <span class="panel-title">调试通道</span>
            <span class="panel-note">真实 ADB，经 443 透传</span>
          </div>
          <div class="panel-body">
            <div class="btn-row">
              <button type="button" class="btn btn-ghost btn-sm" id="btnBridgeStatus">查状态</button>
              <button type="button" class="btn btn-sm" id="btnBridgeOpen">开桥</button>
              <button type="button" class="btn btn-ghost btn-sm" id="btnBridgeClose">关桥</button>
            </div>
            <div class="result" id="bridgeResult"></div>
            <p class="muted" style="margin-top:12px;font-size:12px">
              开桥后在本机执行 <code class="mono">adb connect &lt;服务器&gt;:&lt;端口&gt;</code>：
              <code class="mono">adb install</code>、<code class="mono">push/pull</code>、<code class="mono">logcat</code>
              这类需要 ADB 协议本身的事，都走这条路。
            </p>
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
