/**
 * 剪贴板复制的唯一实现。
 *
 * 四个视图都要「点一下复制」，各写一份的结果是成功与失败的文案、退路各不相同——
 * 同一个操作在审计页说「复制失败」，在设备页却是「已选中」。
 *
 * 约定：
 * - 成功一律 toast；`label` 补宾语（如 `已复制文本`），无 label 即「已复制」。
 * - clipboard API 在非安全上下文或未授权时失败：有 input 元素就退到「选中让手动
 *   复制」（直接失败且无提示才让人困惑）；没有 input 就把失败交给调用方的 sink
 *   （`onFail`）——结果区在哪、长什么样，是视图自己的事。
 */
import { $ } from './dom.js'
import { toast } from './toast.js'

export async function copyText(text, { label = '', fallbackInputId = '', onFail = null } = {}) {
  try {
    await navigator.clipboard.writeText(text)
    toast(`已复制${label}`)
  } catch {
    if (fallbackInputId) {
      $(fallbackInputId).select()
      toast('已选中，按 Ctrl+C 复制')
      return
    }
    if (onFail) {
      onFail()
      return
    }
    toast('复制失败（浏览器未授予剪贴板权限）')
  }
}
