/**
 * 跨端契约常量。**本文件由 tools/sync_contracts.py 生成，不要手改**。
 *
 * 来源：服务端的 `core/actions.py`、`doors/schemas.py`、`modules/config/spec.py`。
 * 重新生成：`cd server && python -m tools.sync_contracts --write`。
 */

/** 喷头的七种状态取值（与服务端 `DoorState` 一致）。 */
export const DOOR_STATES = Object.freeze([
  'free',
  'waiting',
  'bathing',
  'paused',
  'selected',
  'repair',
  'disabled',
])

/** 命令结果状态 → 中文名。取值与中文都取自服务端的 core/outcomes.py。 */
export const OUTCOME_LABELS = Object.freeze({
  'ok': '成功',
  'failed': '失败',
  'timeout': '超时',
  'rejected': '被拒',
})

/** 推送事件类型。取值取自服务端的 core/events.py。 */
export const EVENT_TYPES = Object.freeze({
  DEVICE_UPDATE: 'device.update',
  DOOR_UPDATE: 'door.update',
})

/** 系统按键（与服务端 `DeviceUiKeyRequest.key` 一致）。 */
export const UI_KEYS = Object.freeze([
  'back',
  'home',
  'recents',
  'notifications',
  'dpad_up',
  'dpad_down',
  'dpad_left',
  'dpad_right',
  'dpad_center',
  'enter',
  'del',
])

/** 文本注入的两条通路（clipboard 支持中文；keys 仅 ASCII）。 */
export const INPUT_CHANNELS = Object.freeze([
  'clipboard',
  'keys',
])

/** 滚动方向。 */
export const SCROLL_DIRECTIONS = Object.freeze([
  'forward',
  'backward',
])
