/**
 * 设备作用域编辑器：「全部设备」开关 + 按名勾选，登记与编辑共用同一形状。
 *
 * 身份页的设备作用域与语音页的规则目标都用它。data-* 属性是它自己的契约，
 * 使用方只经 [readScopeEditor] 取值，不伸手进容器里翻输入框。
 *
 * 开关联动（切到全部设备时禁用并全选设备勾选，切回指定模式恢复可选）由
 * [bindScopeToggle] 以事件委托实现，容器重渲染后依旧生效。
 */
import { esc, listen } from './dom.js'

/**
 * 渲染作用域编辑器。
 *
 * @param {Array<{device_id: string, name?: string}>} devices 设备清单（展示名优先）
 * @param {string[]} selected 已选作用域：`['*']` 或设备 id 列表
 */
export function scopeEditorHtml(devices, selected) {
  const wildcard = selected.includes('*')
  const boxes = devices
    .map((device) => {
      const on = wildcard || selected.includes(device.device_id)
      return `<label class="field-row">
          <input type="checkbox" data-scope-device="${esc(device.device_id)}" ${on ? 'checked' : ''} ${wildcard ? 'disabled' : ''}>
          <span>${esc(device.name || device.device_id)}</span>
        </label>`
    })
    .join('')
  return `<div data-scope-container class="check-group">
      <label class="field-row">
        <input type="checkbox" data-scope-wildcard ${wildcard ? 'checked' : ''}>
        <span>全部设备</span>
      </label>
      <div class="btn-row">${boxes || '<span class="muted">暂无设备</span>'}</div>
    </div>`
}

/**
 * 从作用域编辑器容器读出提交值。
 *
 * @returns {string[] | null} `['*']` 或设备 id 列表；指定模式下一台都没选时为
 *   null——「什么都不选」没有含义，由调用方报错，这里不猜。
 */
export function readScopeEditor(container) {
  if (container.querySelector('[data-scope-wildcard]').checked) return ['*']
  const picked = [...container.querySelectorAll('input[data-scope-device]:checked')].map(
    (input) => input.dataset.scopeDevice,
  )
  return picked.length > 0 ? picked : null
}

/**
 * 挂「全部设备」开关联动；返回解绑函数，交给视图的 disposer。
 *
 * @param {ParentNode} root 委托挂载点（视图根），重渲染容器后联动依旧生效
 */
export function bindScopeToggle(root) {
  return listen(root, 'change', (event) => {
    if (!event.target.matches('[data-scope-wildcard]')) return
    const container = event.target.closest('[data-scope-container]')
    const on = event.target.checked
    for (const box of container.querySelectorAll('input[data-scope-device]')) {
      box.disabled = on
      if (on) box.checked = true
    }
  })
}
