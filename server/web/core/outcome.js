/**
 * 命令结果的呈现：状态的中文名，以及"该显示哪句话"。
 *
 * 概览、设备、审计三个视图都要显示 `session.command_result`，集中在这里避免各写一份。
 *
 * ## 为什么不显示 reason_code
 *
 * `reason_code` 是给机器分支用的（登记表在服务端的 `core/reason_codes.py`），
 * 拿它当提示会把 `device.a11y_unavailable` 这种字符串摊在用户面前。人看的是
 * `terminal_message`，服务端已保证它非空（见 `sessions/service.py` 的 `_with_message`）。
 *
 * 唯一的例外是审计页——那是开发者页面，读它的人正需要拿着 code 去日志里搜。
 */

import { OUTCOME_LABELS } from './contract.js'
import { $ } from './dom.js'
import { toast } from './toast.js'

/** 状态 → 徽标样式。未知状态按失败显示：看着像成功比看着像失败危险得多。 */
export const OUTCOME_BADGE = {
  ok: 'badge-green',
  failed: 'badge-red',
  rejected: 'badge-amber',
  timeout: 'badge-amber',
}

/** 状态的中文名；没见过的取值原样回显，让人看得见是哪一个。 */
export function statusLabel(status) {
  return OUTCOME_LABELS[status] || status || '未完成'
}

/** 状态的徽标样式。 */
export function statusBadgeClass(status) {
  return OUTCOME_BADGE[status] || 'badge-red'
}

/** 状态 → 显示前缀。与 [OUTCOME_LABELS] 分开：那个是状态名（用在表里），
 * 这个是已经发生的事（用在一句话的开头）。"被拒"当句子前缀不通顺。 */
const OUTCOME_PREFIX = {
  ok: '已完成',
  failed: '失败',
  rejected: '被拒',
  timeout: '超时',
}

/**
 * 一条结果里最该显示给用户的那句话。
 *
 * 格式统一为 `前缀 · 说明`，成功与失败都一样：设备给的原文（浴室 App 界面上的话）
 * 单看它分不出这次到底成没成。
 *
 * `terminal_message` 为空时只显示前缀。
 */
export function outcomeText(result) {
  const prefix = OUTCOME_PREFIX[result.status] || '未完成'
  const message = result.terminal_message
  return message ? `${prefix} · ${message}` : prefix
}

/**
 * HTTP 层失败的文案。
 *
 * 与服务端命令结果的格式对齐（`前缀 · 说明`）：两者都会落在同一个结果区里，格式若不同，
 * 看的人得先猜"这次是哪种失败"。`error.message` 已经是完整的一句（服务端 problem 的
 * `detail`，如"门 2 当前状态：selected"）。
 */
export function problemText(error) {
  return `失败 · ${error.message}`
}

/**
 * 往结果区写一句话；`ok` / `err` 时另弹一条浮层。
 *
 * **结果区在页面底部，而按钮在上面**：页面一长就得往下滚，滚下去就看不见按钮、也看不见
 * 结果。所以终态一律再弹一条浮层——它固定在视口上，滚到哪儿都看得见；结果区留着当可回看
 * 的记录，两者不重复也不冲突。
 *
 * `info` 那一档（"正在…"）不弹，否则每次操作都先弹一条再弹结果。
 *
 * `id` 为空时只弹浮层、不写结果区——浮层面板没有结果区的位置。
 * `kind` 取 `ok` / `err` / `info`，与样式表的 `.result.ok` / `.result.err` 对应。
 */
export function report(id, text, kind) {
  const box = id ? $(id) : null
  if (box) {
    box.className = `result ${kind}`
    box.textContent = text
  }
  if (kind !== 'info') toast(text)
}
