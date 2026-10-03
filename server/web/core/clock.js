/**
 * 时间与时长：基准、格式化。
 *
 * 全站唯一一份。时长的基准是"现在减开始时刻"，而设备时间已按服务端时钟校准过
 * （docs/DECISIONS.md）。若用浏览器时钟，两边基准不一致，显示的时长会整体偏一个
 * 固定值，而看的人无从察觉。
 *
 * 对时取自 HTTP 响应的 `Date` 头：它由服务端产生，不需要为此加接口。精度到秒，而这里
 * 要消除的偏差以分钟计，够用。
 */
import { PLACEHOLDER } from './dom.js'

let skewMs = 0

/** 两位补零。下面几个格式化函数共用——各写一份时改了一处忘两处。 */
const pad2 = (n) => String(n).padStart(2, '0')

/** 用一次响应对时。任何一次请求都会顺带刷新，不需要专门去调。 */
export function syncClock(response) {
  const header = response.headers.get('date')
  if (!header) return
  const serverTime = new Date(header).getTime()
  if (!Number.isNaN(serverTime)) skewMs = Date.now() - serverTime
}

/** 服务端此刻的时间戳（毫秒）。所有"现在"都必须从这里取，不用 `Date.now()`。
 *
 * 只除一处：`syncClock` 自己算偏差时要用原始时钟，那是它的定义。
 */
export function serverNowMs() {
  return Date.now() - skewMs
}

/** 一个时刻转 `HH:MM:SS`；缺值或解析不出来时给占位符。
 *
 * 参数可以是 RFC 3339 字符串，也可以是毫秒时间戳——顶栏时钟要传后者。
 */
export function fmtTime(value) {
  if (value === null || value === undefined || value === '') return PLACEHOLDER
  const at = new Date(value)
  if (Number.isNaN(at.getTime())) return PLACEHOLDER
  return `${pad2(at.getHours())}:${pad2(at.getMinutes())}:${pad2(at.getSeconds())}`
}

/**
 * 服务端此刻的 `HH:MM:SS`。顶栏时钟用。
 *
 * 单独给入口而不让调用方拼 `fmtTime(new Date(serverNowMs()))`：那样容易顺手写成
 * `fmtTime(new Date())`，显示的是浏览器时间，与设备日志对齐时恒差本机偏差。
 */
export function fmtServerTimeNow() {
  return fmtTime(serverNowMs())
}

/**
 * RFC 3339 转 `MM-DD HH:MM:SS`；缺值或解析不出来时给占位符。
 *
 * 月日 + 秒。"上次使用"与"最后上报"都可能发生在几天前，只给时分看不出是哪天；
 * 而秒是这类信息的精度——"最后上报 3 秒前"与"1 分钟前"对判断链路还活着没有是
 * 两回事。
 *
 * 需要年份的地方（审计流水、发布记录）用 [fmtFullDateTime]。
 */
export function fmtDateTime(value) {
  if (!value) return PLACEHOLDER
  const at = new Date(value)
  if (Number.isNaN(at.getTime())) return PLACEHOLDER
  return (
    `${pad2(at.getMonth() + 1)}-${pad2(at.getDate())} ` +
    `${pad2(at.getHours())}:${pad2(at.getMinutes())}:${pad2(at.getSeconds())}`
  )
}

/**
 * RFC 3339 转 `YYYY-MM-DD HH:MM:SS`；缺值或解析不出来时给占位符。
 *
 * 与 [fmtDateTime] 的差别只有年份：审计流水与发布记录要跨月甚至跨年比对，没有年份
 * 就排不出先后。不用 `toLocaleString`：那会随浏览器的区域设置变化，同一份记录在不同
 * 机器上显示成不同样子，而这里要的正是可比对。
 */
export function fmtFullDateTime(value) {
  if (!value) return PLACEHOLDER
  const at = new Date(value)
  if (Number.isNaN(at.getTime())) return PLACEHOLDER
  return (
    `${at.getFullYear()}-${pad2(at.getMonth() + 1)}-${pad2(at.getDate())} ` +
    `${pad2(at.getHours())}:${pad2(at.getMinutes())}:${pad2(at.getSeconds())}`
  )
}

/**
 * 从开机时刻算出的"已运行多久"，给到分钟。
 *
 * 用 [serverNowMs] 而不是 `Date.now()`：`bootedAt` 是服务端时刻，拿浏览器时钟去减它
 * 会把运行时长整体算偏。只给到分钟：面板是点开看一眼就收起来的东西，秒级跳动既看
 * 不出来也没有意义。
 */
export function fmtUptime(bootedAt) {
  if (!bootedAt) return PLACEHOLDER
  const at = new Date(bootedAt).getTime()
  if (Number.isNaN(at)) return PLACEHOLDER
  const total = Math.max(0, Math.floor((serverNowMs() - at) / 1000))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (days > 0) return `${days} 天 ${hours} 小时`
  if (hours > 0) return `${hours} 小时 ${minutes} 分`
  return `${minutes} 分`
}

/**
 * 设备端墙钟的当前读数（毫秒）。
 *
 * 偏差的符号约定是「服务端时刻 − 设备时刻」（见服务端 `core/device_clock.py`），
 * 所以设备此刻 = 服务端此刻 − 偏差。用服务端此刻而不是设备报来的原值：原值只在那一帧
 * 到达时有效，而面板是随时点开的，读到的是它此刻该显示什么。
 */
export function deviceNowMs(skewSeconds) {
  return serverNowMs() - (Number(skewSeconds) || 0) * 1000
}

/**
 * 偏差的人读说法：`准` / `慢 6 秒` / `快 1 分 30 秒`。
 *
 * 正值表示设备慢——它落在服务端时刻之前。小于 1 秒就当对准了：这台设备的钟自然漂移是
 * 8.5 秒/天，这个量级以下显示小数没有意义，反而让人以为每次刷新都在变。
 */
export function fmtSkew(skewSeconds) {
  const skew = Number(skewSeconds) || 0
  const total = Math.round(Math.abs(skew))
  if (total < 1) return '准'
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  const parts = []
  if (hours > 0) parts.push(`${hours} 小时`)
  if (minutes > 0) parts.push(`${minutes} 分`)
  // 小时级偏差下秒没有意义，省掉它免得行太长
  if (seconds > 0 && hours === 0) parts.push(`${seconds} 秒`)
  return `${skew > 0 ? '慢' : '快'} ${parts.join(' ')}`
}

/**
 * 秒 → `2m30s` / `45s`，与小程序 `formatDuration` 一致。
 *
 * 零值给 `0s` 而不是占位符：这个函数只在需要显示时长时被调用，一个刚分配的喷头
 * 确实是"0 秒"，写成占位符会让它看起来像缺数据。
 */
export function fmtDuration(seconds) {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  return minutes > 0 ? `${minutes}m${pad2(rest)}s` : `${rest}s`
}

/** 从某时刻到服务端此刻的秒数；缺值或解析不出来时给 0。 */
export function elapsedSince(from) {
  if (!from) return 0
  const at = new Date(from).getTime()
  return Number.isNaN(at) ? 0 : Math.max(0, (serverNowMs() - at) / 1000)
}
