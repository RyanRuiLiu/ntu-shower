/**
 * 命令结果的呈现：状态的中文名，以及"该显示哪句话"。
 *
 * 概览、设备、审计三个视图都要显示 `session.command_result`，各写一份必然分叉——
 * 历史上它们对同一个 `failed` 就给出过两种说法。
 *
 * ## 为什么不显示 reason_code
 *
 * `reason_code` 是**给机器分支用的**（登记表在服务端的 `core/reason_codes.py`），
 * 拿它当提示会把 `device.a11y_unavailable` 这种字符串摊在用户面前。人看的是
 * `terminal_message`，服务端已保证它非空（见 `sessions/service.py` 的 `_with_message`）。
 *
 * 唯一的例外是审计页——那是开发者页面，读它的人正需要拿着 code 去日志里搜。
 */

/** 状态 → 中文。取值与设备端的回报一致。 */
export const OUTCOME_STATUS = {
  ok: '成功',
  failed: '失败',
  rejected: '被拒',
  timeout: '超时',
}

/** 状态 → 徽标样式。未知状态按失败显示：看着像成功比看着像失败危险得多。 */
export const OUTCOME_BADGE = {
  ok: 'badge-green',
  failed: 'badge-red',
  rejected: 'badge-amber',
  timeout: 'badge-amber',
}

/** 状态的中文名；没见过的取值原样回显，让人看得见是哪一个。 */
export function statusLabel(status) {
  return OUTCOME_STATUS[status] || status || '未完成'
}

/** 状态的徽标样式。 */
export function statusBadgeClass(status) {
  return OUTCOME_BADGE[status] || 'badge-red'
}

/**
 * 一条结果里最该显示给用户的那句话。
 *
 * 成功时**带上"已完成"前缀**。设备的 `terminal_message` 是它从浴室 App 界面上读到的
 * 原文（`请使用1号喷头`、`当前卡余额：51.77元`），那是给站在喷头前的人看的——单看
 * 它分不出这次操作是成功了，还是在提示你去做别的。
 *
 * 失败时直接用它：设备给的失败说明都是成句的中文（"该位置没有可点击的节点"），
 * 比按状态码套一句通用话有用得多。真为空才退回状态名。
 */
export function outcomeText(result) {
  const message = result.terminal_message
  if (result.status === 'ok') return message ? `已完成 · ${message}` : '已完成'
  return message || statusLabel(result.status)
}
