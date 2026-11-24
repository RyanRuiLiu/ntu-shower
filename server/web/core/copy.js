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
 * 今晚延长服务的界面文案。产品专有词「今晚延长服务」；状态词只在「生效中 / 待生效 /
 * 未在延长中」之间选，其余术语与 ADR-0025 的术语表一致：触发窗、关机时刻（close）、
 * 恢复时刻、整夜覆盖布防、拨钟、常规电源计划。界面不出现 close、hold、对账器等内部词。
 * 同一状态在状态行与结果区只使用这里的同一句话，避免措辞分叉。
 */
export const HOLD_COPY = {
  reading: '今晚延长服务：读取中…',
  none: '今晚延长服务：未设置',
  failed: '今晚延长服务：状态读取失败',
  statusReadFailed: '今晚延长服务状态读取失败',
  notSelected: '今晚延长服务：未选择设备',
  setting: '正在设置今晚延长服务…',
  resuming: '正在恢复常规电源计划…',
  applied: (source, resumeAt, deadline, offline) =>
    `今晚延长服务：生效中（${source}）· 恢复时刻 ${resumeAt} · MCU 布防截止 ${deadline}${offline}`,
  arming: (source, offline) => `今晚延长服务：待生效（${source}）· 布防已写入，拨钟未完成${offline}`,
  pending: (source, offline) => `今晚延长服务：待生效（${source}）${offline}`,
  buttonApplied: '延长中',
  buttonPending: '待生效',
  buttonNone: '今晚延长服务',
  buttonResume: '恢复计划',
  confirmExisting: (state) =>
    `该设备当前处于今晚延长服务中（${state === 'applied' ? '生效中' : '待生效'}）。`
    + '本次点击是幂等操作，不会新增一晚；如需恢复请用「恢复计划」。是否继续？',
  confirmOnline:
    '今晚延长服务？设备钟将被拨快并清空在场会话：名单内的学生自动重分配回原喷头，'
    + '其他人需要重新进入；恢复常规电源计划在恢复时刻（午夜后 10 分钟）且无人占用后自动进行。'
    + '若今晚的关机时刻已过，本次延长作用于明晚。',
  confirmOffline:
    '设备当前离线或已关机。今晚延长服务只能先建立意图，待设备回连后的触发窗'
    + '（关机前 10 分钟）生效；若今晚的关机时刻已过，本次延长作用于明晚。无法立即开机。是否继续？',
  resultApplied: '已在延长中 · 本次点击未改变状态',
  resultArming: '今晚延长服务待生效 · 布防已写入，拨钟未完成',
  resultPending: '今晚延长服务待生效 · 将在关机前的触发窗内生效',
  resultOffline: ' · 设备离线，无法立即开机',
  source: (manual) => (manual ? '手动' : '自动'),
  offlineSuffix: ' · 设备离线',
  resumeOutcome: {
    resumed: '已完成 · 已恢复常规电源计划',
    cancelled: '已完成 · 已取消尚未生效的今晚延长服务',
    'not-holding': '当前不在延长中，无需恢复',
  },
  overview: {
    empty: '当前没有设备在延长中',
    state: (state) => (state === 'applied' ? '生效中' : '待生效'),
    pending: (state) => (state === 'arming' ? '布防已写入，拨钟未完成' : '等待触发窗'),
    pastClose: { yes: '恢复时刻已过', no: '恢复时刻未到' },
    idle: { yes: '喷头空闲', no: '仍有会话' },
    soon: '即将恢复',
    resumeAt: '恢复时刻',
    armingDeadline: 'MCU 布防截止',
    reassigned: '已重分配',
    pendingReassign: '待重分配',
  },
  lock: '今晚延长服务已建立（含待生效）：不能修改时间或重启；先恢复计划，无法恢复时用调试终端执行。',
}
