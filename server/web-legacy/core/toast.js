/**
 * 浮层提示。
 *
 * 只用于「已保存」「已清空」这类不需要用户决策的反馈。它自己会消失，用户没看到就
 * 等于没提示——需要用户确认或需要留痕的信息，放到对应区域的结果区里。
 */
import { $ } from './dom.js'

let hideTimer = null

export function toast(text) {
  const el = $('toast')
  el.textContent = text
  el.classList.add('is-visible')
  clearTimeout(hideTimer)
  hideTimer = setTimeout(() => el.classList.remove('is-visible'), 1800)
}
