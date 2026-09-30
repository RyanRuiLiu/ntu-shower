/**
 * 时间与时长：基准、格式化。
 *
 * **全站唯一一份。** 时长的基准是"现在减开始时刻"，而设备时间已按服务端时钟校准过
 * （docs/DECISIONS.md）。若用浏览器时钟，两边基准就不一致，显示的时长会整体偏一个固定
 * 值——本机时钟快三分钟，每个"分配时长"就都多三分钟，而看的人无从察觉。
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

/** 服务端此刻的时间戳（毫秒）。**所有"现在"都必须从这里取，不用 `Date.now()`。**
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
 * 单独给一个入口而不是让调用方自己拼 `fmtTime(new Date(serverNowMs()))`：那样很容易顺手
 * 写成 `fmtTime(new Date())`——顶栏时钟原本就是这么写的，注释还写着"显示服务端时间"，
 * 而它显示的是浏览器时间，拿它去对设备日志会一直差着几分钟。
 */
export function fmtServerTimeNow() {
  return fmtTime(serverNowMs())
}

/**
 * RFC 3339 转 `MM-DD HH:MM`；缺值或解析不出来时给占位符。
 *
 * 两种粒度并存是刻意的：进行中的会话问的是"今天几点"，`HH:MM:SS` 够用；而"上次使用"
 * 或"最后上报"可能发生在几天前，只给时分看不出是哪天，给完整年份又会把宽度挤掉。
 * 需要年月日的地方（审计、发布）用 [fmtFullDateTime]。
 */
export function fmtDateTime(value) {
  if (!value) return PLACEHOLDER
  const at = new Date(value)
  if (Number.isNaN(at.getTime())) return PLACEHOLDER
  return `${pad2(at.getMonth() + 1)}-${pad2(at.getDate())} ${pad2(at.getHours())}:${pad2(at.getMinutes())}`
}

/**
 * RFC 3339 转 `YYYY-MM-DD HH:MM:SS`；缺值或解析不出来时给占位符。
 *
 * 给要跨天比对的地方用（审计流水、发布记录）。不用 `toLocaleString`：那会随浏览器的
 * 区域设置与版本变化，同一份记录在不同机器上显示成不同样子，而这里要的正是可比对。
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
 * 用 [serverNowMs] 而不是 `Date.now()`：`bootedAt` 是**服务端**时刻，拿浏览器时钟去减
 * 它，本机时钟快几分钟就会把运行时长整体算少几分钟——而看的人无从察觉。这个模块存在的
 * 全部理由就是消除那个偏差。
 *
 * 只给到分钟：面板是点开看一眼就收起来的东西，秒级跳动既看不出来也没有意义。
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
