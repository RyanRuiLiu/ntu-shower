package com.ryanliu.ntushower.features.voice

/** 缓存里的一个音频文件：淘汰只需要名字、大小与最后修改时刻。 */
data class VoiceClipFile(val name: String, val sizeBytes: Long, val lastModifiedMs: Long)

/**
 * 音频缓存淘汰（纯逻辑）。
 *
 * 缓存不能无限涨。按最后修改时刻从旧到新删，当前正在用的文件（[keep]）永远保留；
 * 即便它们更旧，也只删其它文件。播放清单可能含多段音频，必须整组保留。
 */
object VoiceClipCachePolicy {
    fun filesToDelete(files: List<VoiceClipFile>, maxBytes: Long, keep: Set<String>): List<String> {
        var total = files.sumOf { it.sizeBytes }
        if (total <= maxBytes) return emptyList()
        val removed = mutableListOf<String>()
        for (file in files.sortedBy { it.lastModifiedMs }) {
            if (total > maxBytes && file.name !in keep) {
                removed += file.name
                total -= file.sizeBytes
            }
        }
        return removed
    }
}
