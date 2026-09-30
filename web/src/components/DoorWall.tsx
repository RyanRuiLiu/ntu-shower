import { Text, View } from '@tarojs/components'

import type { DoorView } from '../domain/door'
import { DoorPlate } from './DoorPlate'

interface DoorWallProps {
  doors: DoorView[]
  onSelect?: (door: DoorView) => void
}

/**
 * 门牌墙：一台设备的全部喷头。
 *
 * 窄缝网格（`$space-sm`），像瓷砖贴在墙上——不使用"卡片之间的呼吸感"那种
 * 大留白，那会让一屏放不下七个门牌，而用户正需要一眼扫完。
 */
export function DoorWall({ doors, onSelect }: DoorWallProps) {
  if (doors.length === 0) {
    return (
      <View className='wall wall--empty'>
        <Text className='wall__empty-text'>设备尚未上报喷头状态</Text>
      </View>
    )
  }

  return (
    <View className='wall'>
      {doors.map((door) => (
        <DoorPlate key={`${door.device_id}-${door.door_no}`} door={door} onSelect={onSelect} />
      ))}
    </View>
  )
}
