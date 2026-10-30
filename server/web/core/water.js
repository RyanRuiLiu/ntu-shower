/**
 * 水控板状态的唯一一份缓存与刷新入口。
 *
 * 顶栏的“刷新水控板”和设备信息浮层都读这里；概览卡片上的水控标签也只读这里。
 * 之前这份状态放在概览视图内部，顶栏拿不到，才会出现“刷新按钮只在概览卡片里”的局面。
 *
 * 只缓存最近一次 water.status 的结果；换设备清空，不跨设备复用。
 */
import { api } from './api.js'
import { serverNowMs } from './clock.js'
import { NEED_DEVICE } from './copy.js'
import { outcomeText, problemText } from './outcome.js'
import { emit, store, subscribe } from './store.js'

/**
 * 水控板状态与文案的唯一出处。
 *
 * 位义以现场实测为准（见 `docs/evidence/2026-10-30-water-board-2f.md`）：
 * `0x01` 授权位、`0x02` 按钮位、`0x04` 出水位。
 * 正常状态只有 `0x00/0x01/0x02/0x03/0x07`；`0x04–0x06` 现场未出现，
 * `0x08–0x80` 未定义。页面不把位名拼成状态，也不把水控板状态解释成会话状态。
 */
export const WATER_STATE = {
  closed: { label: '已关闭', title: '授权位、按钮位、出水位均为 0' },
  waitButton: { label: '待按电磁阀', title: '授权位已置；现场按一次电磁阀即出水' },
  waitAuth: { label: '待授权', title: '按钮位已置；先授权出水；此时按电磁阀只会关掉按钮位' },
  waitReset: {
    label: '待复位',
    title: '授权位与按钮位都已置，出水位未置；先按一次电磁阀关掉按钮位，再按一次出水',
  },
  flowing: { label: '出水', title: '授权位、按钮位、出水位同时存在，正在出水' },
  unverified: { label: '未验证状态', title: '现场未出现；按位直译，不映射为操作状态' },
  undefinedBits: { label: '未定义位', title: '出现未验证的高位；不映射为操作状态' },
  unknown: { label: '未读取', title: '还没有读到水控板状态' },
}

/** 水控操作的按钮、悬停与结果文案；只在本文件定义，视图不得另写。 */
export const WATER_COPY = {
  authorize: '授权出水',
  cut: '断水',
  authorizeTitle: '置授权位，不建会话；不直接出水，现场按下电磁阀才出水',
  cutTitle: '清授权位与出水位，立即断水；不写浴室 App 会话，按钮位保留',
  authorizeDone: '已授权出水',
  authorizeWaitButton: '已授权出水；现场按下电磁阀才出水',
  authorizeWaitReset: '已授权出水；电磁阀按钮已开，需按两次电磁阀才出水',
  cutDone: '已断水',
  cutButtonOn: '已断水；电磁阀按钮仍开，现场再按一次才回到已关闭',
  notRead: '未读取',
  loading: '读取中…',
  failed: '读取失败',
  refreshing: '正在刷新水控板',
  refreshed: (count) => (count ? `已刷新 ${count} 块水控板` : '未找到水控板'),
}

/** 状态字节 → 一个状态标签；不把位名拼起来。 */
export function waterStatusText(status) {
  if (!Number.isInteger(status)) return WATER_STATE.unknown.label
  if (status & ~0x07) return WATER_STATE.undefinedBits.label
  switch (status & 0x07) {
    case 0x00:
      return WATER_STATE.closed.label
    case 0x01:
      return WATER_STATE.waitButton.label
    case 0x02:
      return WATER_STATE.waitAuth.label
    case 0x03:
      return WATER_STATE.waitReset.label
    case 0x07:
      return WATER_STATE.flowing.label
    default:
      return WATER_STATE.unverified.label
  }
}

/** 状态字节 → 悬停说明；异常组合只写位事实，不编操作状态。 */
export function waterStatusTitle(status) {
  if (!Number.isInteger(status)) return WATER_STATE.unknown.title
  if (status & ~0x07) return WATER_STATE.undefinedBits.title
  switch (status & 0x07) {
    case 0x00:
      return WATER_STATE.closed.title
    case 0x01:
      return WATER_STATE.waitButton.title
    case 0x02:
      return WATER_STATE.waitAuth.title
    case 0x03:
      return WATER_STATE.waitReset.title
    case 0x04:
      return '现场未出现；位事实：未授权、按钮未开、正在出水'
    case 0x05:
      return '现场未出现；位事实：已授权、按钮未开、正在出水'
    case 0x06:
      return '现场未出现；位事实：未授权、按钮已开、正在出水'
    default:
      return WATER_STATE.flowing.title
  }
}

/** 授权出水只在没有授权位时可用；已授权时重复操作会重置授权计时。 */
export function canAuthorize(status) {
  if (!Number.isInteger(status) || (status & ~0x07) !== 0) return false
  const low = status & 0x07
  return low === 0x00 || low === 0x02
}

/** 断水在有授权位或出水位时可用；未读取时保留可用，避免挡住紧急断水。 */
export function canCut(status) {
  if (!Number.isInteger(status)) return true
  return (status & 0x05) !== 0
}

/** 最近一次刷新结果；`at` 为空表示还没成功刷新过。 */
export const water = { deviceId: '', at: '', boards: [], error: '', loading: false }

function setWater(next) {
  Object.assign(water, next)
  emit('water', water)
}

/** 当前设备的水控状态；未刷新或属于别的设备时返回 null。 */
export function waterForDevice() {
  return water.deviceId === store.deviceId && water.at ? water : null
}

/** 逻辑门号 → 板 + 板内门；只查当前设备最近一次刷新。 */
export function waterDoor(doorNo) {
  const current = waterForDevice()
  if (!current) return null
  for (const board of current.boards) {
    const door = (board.doors || []).find((item) => item.door_no === doorNo)
    if (door) return { board, door }
  }
  return null
}

export function subscribeWater(fn) {
  return subscribe('water', fn)
}

/**
 * 读当前设备的水控板信息。返回 `{ ok, message }` 供调用方弹提示；
 * 状态本身已写入 [water]，订阅者会收到。
 */
export async function refreshWater(deviceId = store.deviceId) {
  if (!deviceId) return { ok: false, message: `失败 · ${NEED_DEVICE}` }
  if (water.loading && water.deviceId === deviceId) return { ok: false, message: WATER_COPY.refreshing }
  setWater({ ...water, deviceId, loading: true, error: '' })
  try {
    const result = await api.waterStatus(deviceId)
    if (deviceId !== store.deviceId) return { ok: false, message: '设备已切换，本次结果作废' }
    if (result.status !== 'ok') {
      const message = outcomeText(result)
      setWater({ deviceId, at: '', boards: [], error: message, loading: false })
      return { ok: false, message }
    }
    const boards = result.data?.boards || []
    setWater({
      deviceId,
      at: new Date(serverNowMs()).toISOString(),
      boards,
      error: '',
      loading: false,
    })
    return { ok: true, message: WATER_COPY.refreshed(boards.length) }
  } catch (error) {
    if (deviceId !== store.deviceId) return { ok: false, message: '设备已切换，本次结果作废' }
    const message = problemText(error)
    setWater({ deviceId, at: '', boards: [], error: message, loading: false })
    return { ok: false, message }
  }
}

// 换设备就清空：水控板信息是设备级事实，不能把上一台的结果挂到下一台。
subscribe('device', () => {
  setWater({ deviceId: store.deviceId, at: '', boards: [], error: '', loading: false })
})
