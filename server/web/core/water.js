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
import { outcomeText, problemText } from './outcome.js'
import { emit, store, subscribe } from './store.js'

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
  if (!deviceId) return { ok: false, message: '失败 · 请先选择设备' }
  if (water.loading && water.deviceId === deviceId) return { ok: false, message: '正在刷新水控板' }
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
    return { ok: true, message: `已刷新 ${boards.length} 块水控板` }
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
