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

/** 执行通道：同通道内命令串行，跨通道并行。 */
export const CHANNELS = Object.freeze({
  'session.assign': 'automation',
  'session.settle': 'automation',
  'ui.touch': 'automation',
  'ui.scroll': 'automation',
  'ui.key': 'automation',
  'ui.input': 'automation',
  'ui.dump': 'automation',
  'camera.capture': 'capture',
  'screen.capture': 'capture',
  'device.exec': 'maintenance',
  'device.restart': 'maintenance',
  'device.uninstall': 'maintenance',
  'bridge.open': 'maintenance',
  'bridge.close': 'maintenance',
})
