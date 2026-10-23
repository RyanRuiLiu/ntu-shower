/**
 * 共享状态与订阅。
 *
 * 所有视图从这里读同一份现态，避免各自拉取、各自缓存导致的数据打架。
 * 写入只走本模块导出的 setter——状态散落在各视图里各改一处是注定要出分歧的。
 *
 * 两份设备状态各司其职：
 * - `devices`：精简档案（公开端点），设备下拉与用户面渲染用；
 * - `runtime`：运维档案（admin 端点），设备页诊断表与顶栏详情用；
 *   未填管理身份时为空，各处对缺失字段给占位符。
 *
 * 这里的 `subscribe`/`emit` 是应用事件，与 `core/dom.js` 的 `listen`
 * （DOM 事件）是两回事，命名上刻意分开。
 */
import { api } from './api.js'

/** 上次选择的设备存 localStorage：页面刷新与下次打开都回到同一台。 */
const DEVICE_KEY = 'ntu-shower.device'

function rememberDevice(deviceId) {
  if (deviceId) localStorage.setItem(DEVICE_KEY, deviceId)
  else localStorage.removeItem(DEVICE_KEY)
}

export const store = {
  /** 设备精简档案（公开）。 */
  devices: [],
  /** 设备运维档案（admin）；未持有管理身份时为空数组。 */
  runtime: [],
  /** 当前选中设备的 id；空串表示尚未选中。上次的选择存在 localStorage。 */
  deviceId: localStorage.getItem(DEVICE_KEY) || '',
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

/** 当前选中的设备精简档案；未选中或已不存在时返回 null。 */
export function currentDevice() {
  return store.devices.find((item) => item.device_id === store.deviceId) || null
}

/** 当前选中设备的运维档案；无管理身份或未选中时返回 null。 */
export function currentRuntime() {
  return store.runtime.find((item) => item.device_id === store.deviceId) || null
}

/**
 * 设备的显示名：没有登记名时用设备 ID 本身。
 *
 * 多处要把 device_id 说给人听（表格、徽标、提示），各自写一遍查找就是三份漂移源，
 * 收在这里，与设备清单同一份状态。
 */
export function deviceName(id) {
  const device = store.devices.find((item) => item.device_id === id)
  return device ? device.name || device.device_id : id
}

/**
 * 刷新设备精简清单（公开端点，无需身份）。
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
  if (store.deviceId !== previous) rememberDevice(store.deviceId)
  emit('devices', devices)
  if (store.deviceId !== previous) emit('device', store.deviceId)
  return devices
}

/**
 * 刷新设备运维档案（admin）。无身份或 401 时清空并静默返回 false：
 * 管理身份是用户主动填写的，加载流程不因它的缺失而打断。
 */
export async function refreshRuntime() {
  try {
    store.runtime = await api.deviceRuntime()
  } catch (error) {
    if (error?.status === 401 || error?.status === 403) {
      store.runtime = []
      return false
    }
    throw error
  }
  emit('runtime', store.runtime)
  return true
}

/** 手动切换当前设备。 */
export function selectDevice(deviceId) {
  if (store.deviceId === deviceId) return
  store.deviceId = deviceId
  rememberDevice(deviceId)
  emit('device', deviceId)
}

/**
 * 收到全量喷头数据。行只含门域事实；在线标志取自设备清单（`currentDevice`），
 * 由 `refreshDevices`（加载/重连先于 doors 拉取）与 `upsertDevice`
 * （device.update）维护——同一事实只有一个来源。
 */
export function setDoors(doors) {
  store.doors = doors
  const device = currentDevice()
  if (device) setOnline(Boolean(device.online))
  emit('doors', doors)
}

export function setOnline(online) {
  if (store.online === online) return
  store.online = online
  emit('online', online)
}

/**
 * 收到 device.update（精简载荷：device_id、name、online、at）。
 *
 * 两份状态里属于该设备的那条都要更新：精简清单改 name/online，运维档案改
 * name/online（其余诊断字段等下一次 runtime 轮询补全）。只改一份，下拉与
 * 顶栏就会互相矛盾。
 */
export function upsertDevice(record) {
  const pick = (item) => ({ ...item, name: record.name, online: record.online })
  const summaryIndex = store.devices.findIndex((item) => item.device_id === record.device_id)
  if (summaryIndex === -1) store.devices.push({ device_id: record.device_id, name: record.name, online: record.online })
  else store.devices[summaryIndex] = pick(store.devices[summaryIndex])
  const runtimeIndex = store.runtime.findIndex((item) => item.device_id === record.device_id)
  if (runtimeIndex !== -1) store.runtime[runtimeIndex] = pick(store.runtime[runtimeIndex])
  emit('devices', store.devices)
  emit('runtime', store.runtime)
  if (record.device_id === store.deviceId) setOnline(Boolean(record.online))
}

export function setStreaming(streaming) {
  if (store.streaming === streaming) return
  store.streaming = streaming
  emit('streaming', streaming)
}
