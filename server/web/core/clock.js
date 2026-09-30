/**
 * 服务端时钟基准。
 *
 * 门牌上的时长是"现在减开始时刻"算出来的，而设备时间已按服务端时钟校准过
 * （docs/DECISIONS.md）。若这里用浏览器时钟，两边基准就不一致，显示的时长会整体偏一个固定值——
 * 本机时钟快三分钟，每个"分配时长"就都多三分钟，而看的人无从察觉。
 *
 * 对时取自 HTTP 响应的 `Date` 头：它由服务端产生，不需要为此加接口。精度到秒，
 * 而这里要消除的偏差以分钟计，够用。
 */
let skewMs = 0

/** 用一次响应对时。任何一次请求都会顺带刷新，不需要专门去调。 */
export function syncClock(response) {
  const header = response.headers.get('date')
  if (!header) return
  const serverTime = new Date(header).getTime()
  if (!Number.isNaN(serverTime)) skewMs = Date.now() - serverTime
}

/** 服务端此刻的时间戳（毫秒）。 */
export function serverNowMs() {
  return Date.now() - skewMs
}
