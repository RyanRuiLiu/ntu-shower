import { Text, View } from '@tarojs/components'

import type { DoorView } from '../domain/door'
import { DOOR_STATE_LABEL } from '../domain/door'

interface DoorPlateProps {
  door: DoorView
  /** 点击回调。未提供时为只读展示（未授权用户看到的形态）。 */
  onSelect?: (door: DoorView) => void
}

/**
 * 一块门牌：一个喷头的现态。
 *
 * 形状是**竖向矩形**——真实的浴室隔间就是竖的，不是正方形，也不是卡片。
 * 数字（门号）是主角，状态文字是说明。
 *
 * 颜色由 `door--<state>` 这个类名在 SCSS 里决定（见 `app.scss` 的 `@each`），
 * 组件本身不碰颜色：同一套状态的颜色只应有一处定义。
 */
export function DoorPlate({ door, onSelect }: DoorPlateProps) {
  const state = door.state
  const interactive = Boolean(onSelect)

  return (
    <View
      className={`door door--${state}`}
      onClick={interactive ? () => onSelect?.(door) : undefined}
      role={interactive ? 'button' : undefined}
      aria-label={`${door.door_no} 号 ${DOOR_STATE_LABEL[state]}`}
    >
      <Text className='door__no'>{door.door_no}</Text>
      <Text className='door__state'>{DOOR_STATE_LABEL[state]}</Text>
    </View>
  )
}
