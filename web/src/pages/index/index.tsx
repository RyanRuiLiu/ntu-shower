import { Text, View } from '@tarojs/components'

import { DoorWall } from '../../components/DoorWall'
import { useDoors } from '../../hooks/useDoors'
import './index.scss'

/**
 * 门牌墙页面：本站全部喷头的实时状态。
 *
 * 这是用户最常用的入口——站在浴室门口看一眼，判断有没有空位。
 * 因此页面上不放任何需要先阅读才懂的东西：标题一行，其余全是门牌。
 */
export default function Index() {
  const { doors, loading, error, reload } = useDoors()

  return (
    <View className='page'>
      <View className='page__head'>
        <Text className='page__title'>喷头状态</Text>
        {doors.length > 0 && <Text className='page__count'>{doors.length} 个喷头</Text>}
      </View>

      {loading && <Text className='page__hint'>正在获取…</Text>}

      {error !== null && (
        <View className='page__error' onClick={reload}>
          <Text className='page__error-text'>{error}</Text>
          <Text className='page__error-action'>点击重试</Text>
        </View>
      )}

      {!loading && error === null && <DoorWall doors={doors} />}
    </View>
  )
}
