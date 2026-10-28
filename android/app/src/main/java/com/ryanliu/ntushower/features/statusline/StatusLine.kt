package com.ryanliu.ntushower.features.statusline

import org.json.JSONObject

/** 服务端推送的一台设备现状：楼层名、可用与否、空闲喷头数。 */
data class FloorStatus(val deviceId: String, val name: String, val online: Boolean, val free: Int?)

/** 状态行里的一个片段；[tone] 只在「按状态」配色时决定颜色。 */
data class StatusPart(val text: String, val tone: StatusTone)

/** 片段语义：正文、有空闲、零空闲、不可用（离线或无数据）。 */
enum class StatusTone { PLAIN, AVAILABLE, EMPTY, UNAVAILABLE }

/** 配色方式；与配置登记表 `status_line_color_mode` 的 choices 逐字一致。 */
object StatusLineColorMode {
    const val BY_STATE = "按状态"
    const val UNIFORM = "统一"
}

/** 一次显示所需的全部配置与内容；由控制器在主线程从 RuntimeConfig 与最新推送复制。 */
data class StatusLineSettings(
    val enabled: Boolean,
    val textSizeSp: Int,
    val textColor: String,
    val colorMode: String,
    val background: Boolean,
    val align: String,
    val centerOffsetDp: Int,
    val xDp: Int,
    val yDp: Int,
    val floors: List<FloorStatus>,
)

/** 显隐原因的唯一出处；日志与测试都用这些常量。 */
object StatusLineReason {
    const val VISIBLE = "visible"
    const val DISABLED = "disabled"
    const val COVER_VISIBLE = "cover_visible"
    const val NO_STATUS = "no_status"
    const val STALE = "stale"
}

/**
 * 楼层状态行的内容规则：服务端给结构化的楼层列表，文字在这里拼。
 *
 * **只有空闲数，没有分母**。分母会让人把「3/7」读成「还有 3 台机器可用」，而这里的
 * 7 是喷头总数；只留空闲数就没有这个歧义。0 照写「0」，不写「满」——这一行回答的是
 * 「有没有空位」，不是一句评价。
 *
 * 在线但还没上报喷头（`free == null`）写「无数据」，离线写「离线」：两者与「0 个
 * 空闲」是不同的事实，不能都显示成 0。
 */
object StatusLine {

    const val HEADER = "各楼层空闲 "
    private const val SEPARATOR = " · "
    private const val OFFLINE = "离线"
    private const val NO_DATA = "无数据"

    /** 解析 `display.status` 的 payload；形状不对的楼层丢掉，不因一条坏数据整行不显示。 */
    fun parse(payload: JSONObject): List<FloorStatus> {
        val array = payload.optJSONArray("floors") ?: return emptyList()
        return (0 until array.length()).mapNotNull { index ->
            val item = array.optJSONObject(index) ?: return@mapNotNull null
            val name = item.optString("name")
            if (name.isEmpty()) return@mapNotNull null
            FloorStatus(
                deviceId = item.optString("device_id"),
                name = name,
                online = item.optBoolean("online", false),
                free = if (item.isNull("free")) null else item.optInt("free", 0),
            )
        }
    }

    /** 按服务端给的顺序拼出片段；没有楼层时给空列表，控制器据此隐藏窗口。 */
    fun parts(floors: List<FloorStatus>): List<StatusPart> {
        if (floors.isEmpty()) return emptyList()
        val parts = ArrayList<StatusPart>(floors.size * 2 + 1)
        parts.add(StatusPart(HEADER, StatusTone.PLAIN))
        floors.forEachIndexed { index, floor ->
            if (index > 0) parts.add(StatusPart(SEPARATOR, StatusTone.PLAIN))
            val (value, tone) = when {
                !floor.online -> OFFLINE to StatusTone.UNAVAILABLE
                floor.free == null -> NO_DATA to StatusTone.UNAVAILABLE
                floor.free > 0 -> floor.free.toString() to StatusTone.AVAILABLE
                else -> "0" to StatusTone.EMPTY
            }
            parts.add(StatusPart("${floor.name} $value", tone))
        }
        return parts
    }

    /** 纯文本形式：日志与测试用它，拼接规则与 [parts] 同源。 */
    fun text(floors: List<FloorStatus>): String = parts(floors).joinToString("") { it.text }
}
