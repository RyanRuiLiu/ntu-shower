/**
 * 事件日志：记录前端发生过什么（连接、命令、错误）。
 *
 * 全站一份，任何模块都能写，概览视图订阅它并展示。**这不是审计**——真正的命令
 * 审计在服务端的 `command_audit` 里，那份能查历史。这里只是"现场发生了什么"，
 * 所以有固定上限，不随运行时长增长。
 */
import { emit } from './store.js'

const MAX_ENTRIES = 200
const entries = []

/**
 * 写一条。
 *
 * `level` 取 `ok` / `err` / `warn` / `info`，与样式表的 `.log-*` 一一对应。
 */
export function log(level, text) {
  entries.push({ level, text, at: new Date() })
  if (entries.length > MAX_ENTRIES) entries.shift()
  emit('log', entries)
}

export function clearLog() {
  entries.length = 0
  emit('log', entries)
}

export function getLog() {
  return entries
}
