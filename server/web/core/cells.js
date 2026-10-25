/**
 * 跨视图同义单元格：设备名与不透明标识。
 *
 * 「设备」列同时出现在审计、语音、概览任务板，「学号 / 身份标识」出现在审计、
 * 语音、身份、设备档案。各写一份 markup 的结果不是风格差异，而是同一列在不同
 * 页面上的悬停信息、字形、占位符各不相同——窄屏下尤其明显。
 *
 * 只收这两类跨视图外观必须一致的单元格。时间用 core/clock.js 的格式化函数，
 * 状态用 badge，数字与命令用 .mono：它们的差别本来就该看得出来。
 */
import { esc, PLACEHOLDER } from './dom.js'
import { deviceName } from './store.js'

/**
 * 设备名：显示名 + 悬停给设备 ID，单行不折。
 *
 * 设备档案表自己展示 ID（那是它的职责），不走这里。`className` 只给上下文加
 * 已有类名（如任务板用 `task-dev` 加粗实体），不新增样式。
 */
export function deviceCell(deviceId, className = '') {
  if (!deviceId) return PLACEHOLDER
  const extra = className ? ` ${esc(className)}` : ''
  return `<span class="nowrap${extra}" title="${esc(deviceId)}">${esc(deviceName(deviceId))}</span>`
}

/**
 * 学号、身份标识等不透明标识：全站统一 `<code>`，与设备档案里的 ID 同一种字形。
 *
 * 单行不折：标识被折成两行后，复制或对照时容易读错。
 */
export function codeCell(value) {
  return `<code class="nowrap">${esc(value ?? PLACEHOLDER)}</code>`
}
