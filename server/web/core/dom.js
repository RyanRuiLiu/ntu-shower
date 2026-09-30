/**
 * DOM 与文本工具。
 *
 * 全站唯一一份。视图模块里不得再出现 `document.getElementById`、自写的转义函数
 * 或自拼的类名——这些东西各写一份，样式与安全性就没有统一标准可依，改一处忘三处
 * 是必然的。
 */

/**
 * 缺值时的占位符。
 *
 * 全站一个：此前有 `--`、"未命名"、"从未使用" 与**空白**四种写法，同一块界面上
 * 看起来像四种不同的问题。空白最糟——它让人分不清"这个字段没有值"与"渲染漏了"。
 *
 * 用破折号而不是文字：占位只该说明"这里没有内容"，不该编出一个像事实的说法
 * （"从未使用"就是那种——它断言了一件我们从数据里读不出来的事）。
 */
export const PLACEHOLDER = '—'

/**
 * 按 id 取元素。
 *
 * 取不到时抛错而不是返回 null：页面结构与代码不一致时，静默的 null 会让问题在几步
 * 之后才以"某个操作没反应"的形式暴露，那时已经很难定位。
 */
export function $(id) {
  const el = document.getElementById(id)
  if (!el) throw new Error(`页面缺少元素 #${id}`)
  return el
}

/**
 * HTML 转义。
 *
 * 设备上来的文本（文件路径、节点 text、错误消息）一律经它再插入 DOM：内容里的
 * `<`、`"` 会改变结构，轻则显示错乱，重则注入。
 */
export function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

/**
 * 挂一个 DOM 事件，返回解绑函数。
 *
 * 返回解绑函数是为了让视图的 `unmount` 能干净收尾——轮询与全局监听忘记解绑会
 * 在视图反复切换后累积，表现为越用越卡。
 */
export function listen(target, type, handler) {
  target.addEventListener(type, handler)
  return () => target.removeEventListener(type, handler)
}

/** 把一组「解绑函数」收集起来，视图卸载时一次性调用。 */
export function disposer() {
  const pending = []
  return {
    add(fn) {
      pending.push(fn)
    },
    dispose() {
      while (pending.length > 0) {
        try {
          pending.pop()()
        } catch {
          // 解绑失败不该阻断其余解绑
        }
      }
    },
  }
}
