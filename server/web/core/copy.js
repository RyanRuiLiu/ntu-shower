/**
 * 跨视图共用的界面文案。
 *
 * 同一个操作在几处出现时，句子必须逐字一致——读的人按同一句话找同一个操作，措辞一旦
 * 分叉，就会被当成两件事。视图形状不同（按钮、状态栏、空状态）不改变句子本身。
 */

/** 设备是多数操作的前提；顶栏是选择设备的位置，句子写全，不省略。 */
export const NEED_DEVICE = '请先在顶栏选择设备'

/** 重启前的确认：整机离线与进行中的会话会中断，两条后果都要说。 */
export const REBOOT_CONFIRM = '重启设备？整机会重启，期间设备离线，进行中的会话会中断。'

/**
 * 今晚延长的界面文案。产品专有词「今晚延长」不替换；其余词与 ADR-0025 一致：
 * 触发窗、恢复时刻、整夜覆盖布防、拨钟、常规计划、尚未施加/hold 中/未在延长中。
 * 同一状态在状态行与结果区只使用这里的同一句话，避免措辞分叉。
 */
export const HOLD_COPY = {
  reading: '今晚延长：读取中…',
  none: '今晚延长：未设置',
  failed: '今晚延长：状态读取失败',
  notSelected: '今晚延长：未选择设备',
  applied: (source, resumeAt, deadline, offline) =>
    `今晚延长：生效中（${source}）· 恢复时刻 ${resumeAt} · MCU 布防截止 ${deadline}${offline}`,
  arming: (source, offline) => `今晚延长：待生效（${source}）· 布防已写入，拨钟未完成${offline}`,
  pending: (source, offline) => `今晚延长：待生效（${source}）· 尚未施加${offline}`,
  buttonApplied: '延长生效中',
  buttonPending: '延长待生效',
  buttonNone: '今晚延长',
  confirmExisting: (state) =>
    `该设备当前已在今晚延长状态（${state === 'applied' ? '生效中' : '待生效'}）。`
    + '本次点击是幂等操作，不会新增一晚；如需恢复请用「恢复计划」。是否继续？',
  confirmOnline:
    '今晚延长？设备钟将被偏置并清空在场会话：名单内的学生自动重分配回原喷头，'
    + '其他人需要重新进入；恢复在恢复时刻（午夜后 10 分钟）且无人占用后自动进行。'
    + '若设备已关机，本次只能预约下一次触发窗，无法立即开机。',
  confirmOffline:
    '设备当前离线或已关机。今晚延长只能先建立意图，待设备回连后的触发窗'
    + '（close 前 10 分钟）施加；若今晚 close 已过，目标是下一个 close。无法立即开机。是否继续？',
  resultApplied: '已在延长中 · 本次点击未改变状态',
  resultArming: '延长进行中 · 布防已写入，拨钟未完成',
  resultPending: '今晚延长待生效 · 对账器会在触发窗内施加',
  resultOffline: ' · 设备离线，无法立即开机',
  resumeOutcome: {
    resumed: '已完成 · 已恢复常规计划',
    cancelled: '已完成 · 已取消尚未生效的今晚延长',
    'not-holding': '当前不在延长中，无需恢复',
  },
  resumeFallback: '已完成 · 已处理恢复请求',
  lock: '今晚延长已建立（含待生效）：不能修改时间或重启；先恢复计划，无法恢复时用调试终端执行。',
}
