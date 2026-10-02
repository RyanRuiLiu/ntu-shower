/**
 * 发布：OTA 的注册与下架。
 *
 * 上传走 multipart，不能预设 Content-Type——boundary 必须由浏览器自己生成，
 * 这一点由 `core/api.js` 的 `raw` 选项处理，这里只管把 `FormData` 递过去。
 *
 * ## 两个开关的语义
 *
 * - `mandatory`：设备端视为必须安装（当前实现未强制，但记录在案）
 * - `force`：无视版本号，调试期反复推同一版本用。正式升级不要勾——它会让所有
 *   目标设备重新装一遍，即使版本号没变
 *
 * 下架是紧急止损：发出去才发现有问题时，用它终止后续设备继续安装。
 */
import { api } from '../core/api.js'
import { problemText, report } from '../core/outcome.js'
import { fmtFullDateTime } from '../core/clock.js'
import { $, disposer, esc, listen } from '../core/dom.js'
import { store, subscribe } from '../core/store.js'
import { toast } from '../core/toast.js'

function render(list) {
  if (list.length === 0) {
    $('releaseTable').innerHTML = '<div class="empty">暂无发布记录</div>'
    return
  }
  const rows = list
    .map(
      (item) => `
      <tr data-version="${esc(item.version)}">
        <td class="mono">${esc(item.version)}</td>
        <td class="mono">${esc(item.version_code)}</td>
        <td>${item.force ? '<span class="badge badge-amber">强制</span>' : ''}${item.mandatory ? '<span class="badge badge-blue">必需</span>' : ''}</td>
        <td>${esc(item.target)}</td>
        <td class="mono muted" title="${esc(item.sha256)}">${esc(item.sha256.slice(0, 12))}…</td>
        <td class="mono">${esc(fmtFullDateTime(item.created_at))}</td>
        <td>
          <div class="btn-row">
            <button type="button" class="btn btn-ghost btn-sm" data-act="rearm">重新推送</button>
            <button type="button" class="btn btn-danger btn-sm" data-act="delete">下架</button>
          </div>
        </td>
      </tr>`,
    )
    .join('')
  $('releaseTable').innerHTML = `<table class="table"><thead><tr>
      <th>版本名</th><th>版本号</th><th>标记</th><th>目标</th><th>摘要</th><th>发布时间</th><th>操作</th>
    </tr></thead><tbody>${rows}</tbody></table>`
}

async function reload() {
  try {
    render(await api.releases())
  } catch (error) {
    report('releaseResult', problemText(error), 'err')
  }
}

async function upload() {
  const file = $('apkFile').files?.[0]
  const version = $('inVersion').value.trim()
  const versionCode = Number($('inVersionCode').value)
  if (!file || !version || !Number.isFinite(versionCode)) {
    report('releaseResult', '失败 · 请选择 APK 文件，并填写版本名与版本号', 'err')
    return
  }
  const form = new FormData()
  form.set('version', version)
  form.set('version_code', String(versionCode))
  form.set('file', file)
  if ($('inForce').checked) form.set('force', 'true')
  if ($('inTarget').value.trim()) form.set('target', $('inTarget').value.trim())

  report('releaseResult', '正在上传…', 'info')
  try {
    const result = await api.uploadRelease(form)
    report('releaseResult', `已发布 ${result.version}，已通知 ${result.notified_devices.length} 台设备`, 'ok')
    await reload()
  } catch (error) {
    report('releaseResult', problemText(error), 'err')
  }
}

let dispose = disposer()

export const releasesView = {
  id: 'releases',
  title: '发布',

  mount(root) {
    root.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <span class="panel-title">已发布版本</span>
          <div class="panel-tools">
            <button type="button" class="btn btn-ghost btn-sm" id="btnReload">刷新</button>
          </div>
        </div>
        <div class="panel-body flush" id="releaseTable"></div>
      </section>

      <section class="panel">
        <div class="panel-head"><span class="panel-title">上传新版本</span></div>
        <div class="panel-body">
          <div class="grid grid-3">
            <div class="field">
              <label for="inVersion">版本名（形如 0.3.16）</label>
              <input id="inVersion" type="text">
            </div>
            <div class="field">
              <label for="inVersionCode">版本号（整数，必须递增）</label>
              <input id="inVersionCode" type="number" min="1">
            </div>
            <div class="field">
              <label for="apkFile">APK 文件</label>
              <input id="apkFile" type="file" accept=".apk">
            </div>
          </div>
          <div class="field">
            <label for="inTarget">目标设备（留空表示全部设备，或填写设备 ID）</label>
            <input id="inTarget" type="text" placeholder="留空表示全部设备">
          </div>
          <label class="field-row" style="margin-bottom:12px">
            <input id="inForce" type="checkbox">
            <span>强制推送<span class="muted"> · 忽略版本号，仅用于调试</span></span>
          </label>
          <div class="btn-row">
            <button type="button" class="btn btn-sm" id="btnUpload">发布</button>
          </div>
          <div class="result" id="releaseResult"></div>
        </div>
      </section>`

    dispose = disposer()
    dispose.add(subscribe('view', (id) => { if (id !== 'releases') dispose.dispose() }))
    dispose.add(listen(root.querySelector('#btnReload'), 'click', reload))
    dispose.add(listen($('btnUpload'), 'click', upload))
    dispose.add(
      listen($('releaseTable'), 'click', async (event) => {
        const button = event.target.closest('button[data-act]')
        if (!button) return
        const version = button.closest('tr').dataset.version
        try {
          if (button.dataset.act === 'rearm') {
            if (!store.deviceId) {
              report('releaseResult', '失败 · 请先在顶栏选择设备', 'err')
              return
            }
            const result = await api.rearmRelease(version, store.deviceId)
            report('releaseResult', result.rearmed ? '已清除该设备的安装确认' : '该设备无安装确认记录', 'info')
          } else {
            if (!window.confirm(`下架 ${version}？安装途中的设备将不再接收该版本。`)) return
            await api.deleteRelease(version)
            toast('已下架')
            await reload()
          }
        } catch (error) {
          report('releaseResult', problemText(error), 'err')
        }
      }),
    )

    reload()
  },

  unmount() {
    dispose.dispose()
  },
}
