import type { components } from '../api/schema'

/** 服务端派生的一扇喷头的状态。枚举值来自 OpenAPI（见 schema.d.ts），不在此重复定义。 */
export type DoorState = components['schemas']['DoorState']

/** 一扇喷头的现态（含所属设备的在线状态与名称）。 */
export type DoorView = components['schemas']['DoorView']

/**
 * 状态 → 界面文案。
 *
 * 只放**显示文本**，不放颜色——颜色由 `door--<state>` 这个 class 在 SCSS 里决定
 * （见 `styles/_tokens.scss` 的 `$state-colors`）。这样同一套状态的颜色只有一处定义，
 * 不会出现"JS 里一套色、CSS 里一套色"的漂移。
 *
 * 类型标注为 `Record<DoorState, string>` 是刻意的：服务端新增状态时这里会**编译报错**，
 * 而不是运行时渲染出空白。
 */
export const DOOR_STATE_LABEL: Record<DoorState, string> = {
  free: '空闲',
  waiting: '已入门',
  bathing: '使用中',
  paused: '已暂停',
  selected: '选中',
  repair: '报修',
  disabled: '禁用',
}

/**
 * 该状态是否可用（用户能不能进去洗）。
 *
 * 只有 `free` 可用：站在浴室门口的人只需要这一个判断，其余六种状态对他来说
 * 都是"这扇门现在不能用"。
 */
export function isAvailable(state: DoorState): boolean {
  return state === 'free'
}

/**
 * 该状态是否表示有人占用（区别于"故障停用"）。
 *
 * 用于界面区分"等一会儿"与"别等了"——这两种情况对用户的行动建议完全不同。
 */
export function isOccupied(state: DoorState): boolean {
  return state === 'waiting' || state === 'bathing' || state === 'paused'
}
