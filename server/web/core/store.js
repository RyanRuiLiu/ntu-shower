/**
 * 共享状态与订阅。
 *
 * 所有视图从这里读同一份现态，避免各自拉取、各自缓存导致的数据打架。
 * 写入只走本模块导出的 setter——状态散落在各视图里各改一处是注定要出分歧的。
 *
 * 这里的 `subscribe`/`emit` 是**应用事件**，与 `core/dom.js` 的 `listen`
 * （DOM 事件）是两回事，命名上刻意分开。
 */
import { api } from './api.js'

export const store = {
  /** 设备清单。 */
  devices: [],
  /** 当前选中设备的 id；空串表示尚未选中。 */
  deviceId: '',
  /** 当前设备的喷头现态。 */
  doors: [],
  /** 当前设备的网关是否在线。 */
  online: false,
  /** 实时推送通道是否已连上。 */
  streaming: false,
}

const listeners = new Map()

/** 订阅一个应用事件；返回解绑函数。 */
export function subscribe(event, fn) {
  if (!listeners.has(event)) listeners.set(event, [])
  listeners.get(event).push(fn)
  return () => {
    const group = listeners.get(event) || []
    const index = group.indexOf(fn)
    if (index >= 0) group.splice(index, 1)
  }
}

export function emit(event, payload) {
  // 复制一份再遍历：回调里解绑或再订阅都不该影响本次派发
  for (const fn of [...(listeners.get(event) || [])]) fn(payload)
}

/** 当前选中的设备记录；未选中或已不存在时返回 null。 */
export function currentDevice() {
  return store.devices.find((item) => item.device_id === store.deviceId) || null
}

/**
 * 刷新设备清单。
 *
 * 只在"还没选"或"选中的设备已从清单消失"时才改选择——用户正看着某台设备时，
 * 后台的定期刷新不该把他的选择顶掉。
 */
export async function refreshDevices() {
  const devices = await api.devices()
  const previous = store.deviceId
  store.devices = devices
  const known = devices.some((item) => item.device_id === previous)
  store.deviceId = known ? previous : devices[0]?.device_id || ''
  emit('devices', devices)
  if (store.deviceId !== previous) emit('device', store.deviceId)
  return devices
}

/** 手动切换当前设备。 */
export function selectDevice(deviceId) {
  if (store.deviceId === deviceId) return
  store.deviceId = deviceId
  emit('device', deviceId)
}

/** 收到全量喷头数据。在线状态从数据里推断，理由见旧实现的注释：WS 只推变化。 */
export function setDoors(doors) {
  store.doors = doors
  if (doors.length > 0) setOnline(Boolean(doors[0].device_online))
  emit('doors', doors)
}

export function setOnline(online) {
  if (store.online === online) return
  store.online = online
  emit('online', online)
}

/**
 * 收到网关的上下线通知。
 *
 * **两处都要改**：`store.online`（当前设备，供顶栏那盏灯）与 `store.devices` 里对应
 * 那台的 `online` 字段（供设备下拉里的"（在线）"）。只改前者，下拉项就会与紧挨着它的
 * 那盏灯互相矛盾——清单是每 30 秒拉一次，两处各说各话最长能持续半分钟，而看的人没有
 * 任何线索能判断该信哪一个。
 */
export function setDeviceOnline(deviceId, online) {
  const device = store.devices.find((item) => item.device_id === deviceId)
  if (device && device.online !== online) {
    device.online = online
    emit('devices', store.devices)
  }
  if (deviceId === store.deviceId) setOnline(online)
}

export function setStreaming(streaming) {
  if (store.streaming === streaming) return
  store.streaming = streaming
  emit('streaming', streaming)
}
