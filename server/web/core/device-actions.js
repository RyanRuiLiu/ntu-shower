/**
 * 设备维护里的两个「轻」动作：修改设备端时间、重启浴室 App。
 *
 * 它们在两处出现——设备页的「设备自身」按钮行，和顶栏设备信息面板的功能行。**实现只此
 * 一份**：两处各写一遍，超时与失败文案迟早分叉，而这类文案正是排障时要照着搜日志的。
 *
 * 结果往哪里呈现由调用方决定：设备页报在页内的结果区（那里有位置放详情），顶栏面板报成
 * toast（它是个点开就收的浮层，没有放结果区的地方）。所以这里只负责动作与文案。
 *
 * 更重的两个（重启本程序、重启整机）不在这里：它们会掐断回报通路，确认话术也更重，
 * 只在设备页出现。
 */
import { api } from './api.js'
import { fmtFullDateTime, serverNowMs } from './clock.js'
import { outcomeText, problemText } from './outcome.js'

/** 与服务端 `clock.set` 边界上那条正则同形：客户端先拦一次，省一次往返。 */
const CLOCK_FORMAT = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/

/**
 * 修改设备端系统时间。
 *
 * 默认值取**服务端校准过的当前时间**（`serverNowMs`）而不是本机时间：操作者的本机时钟
 * 可能偏，而设备时间正是要往这个基准上对——给一个准的起点，人只需确认。
 *
 * @param deviceId 目标设备
 * @param say 结果呈现，形如 `(text, kind)`，kind 取 `info` / `ok` / `err`
 */
export function promptClockSet(deviceId, say) {
  const input = window.prompt('设置设备系统时间（格式：YYYY-MM-DD HH:MM:SS）', fmtFullDateTime(serverNowMs()))
  if (!input) return
  if (!CLOCK_FORMAT.test(input.trim())) {
    say('失败 · 时间格式应为 YYYY-MM-DD HH:MM:SS', 'err')
    return
  }
  say('正在设置设备时间…', 'info')
  api
    .clockSet(deviceId, input.trim())
    .then((result) => say(outcomeText(result), result.status === 'ok' ? 'ok' : 'err'))
    .catch((error) => say(problemText(error), 'err'))
}

/**
 * 重启浴室 App：强停会中断进行中的会话，必须先确认。
 *
 * @param deviceId 目标设备
 * @param say 结果呈现，同 [promptClockSet]
 */
export async function restartBathApp(deviceId, say) {
  if (!window.confirm('重启浴室 App？进行中的会话会被中断。')) return
  say('正在重启浴室 App…', 'info')
  try {
    const result = await api.bathRestart(deviceId)
    say(outcomeText(result), result.status === 'ok' ? 'ok' : 'err')
  } catch (error) {
    say(problemText(error), 'err')
  }
}
