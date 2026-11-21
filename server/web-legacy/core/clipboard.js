/**
 * 剪贴板复制的唯一实现。
 *
 * 四个视图都要「点一下复制」，各写一份的结果是成功与失败的文案、错误出口各不相同——
 * 同一个操作在审计页说「复制失败」，在设备页却是「已选中」。
 *
 * 约定：
 * - 成功一律 toast；`label` 补宾语（如 `已复制文本`），无 label 即「已复制」。
 * - clipboard API 失败（未授权等）：结果区在哪、长什么样，是视图自己的事，
 *   调用方用 `onFail` 接走；没传就统一失败 toast。
 */
import { toast } from './toast.js'

export async function copyText(text, { label = '', onFail = null } = {}) {
  try {
    await navigator.clipboard.writeText(text)
    toast(`已复制${label}`)
  } catch {
    if (onFail) {
      onFail()
      return
    }
    toast('复制失败（浏览器未授予剪贴板权限）')
  }
}
