package com.ryanliu.ntushower.features.doors

import com.ryanliu.ntushower.core.clock.Clock
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * 一次 t_doorlist 读数：`doors` 是解析出的行，`complete` 表示是否所有非空行都解析成功。
 *
 * 空表、退出码失败、任一行坏掉都给 `complete = false`。发件箱仍按 `doors` 如实上报；
 * 屏幕提示用 `complete` 判断占用数是否可以作为判定依据。
 */
data class DoorReading(val doors: List<DoorRaw>, val complete: Boolean)

/** t_doorlist 原始行：设备端零派生，字段与数据库列一一对应。 */
data class DoorRaw(
    val doorNo: Int,
    val cardno: Long,
    val certno: String,
    val accno: String,
    val openTime: String,
    val inTime: String,
    val closeTime: String,
    val bathTime: Int,
    val status: Int,
) {

    /** 转为 door.snapshot payload 的 doors[] 元素；时间转 UTC RFC3339（设备本地时区 → UTC）。 */
    fun toPayload(): JSONObject {
        val o = JSONObject()
            .put("door_no", doorNo)
            .put("card_no", cardno)
            .put("cert_no", certno)
            .put("acc_no", accno)
            .put("bath_time_s", bathTime)
            .put("status_raw", status)
        putTime(o, "open_time", openTime)
        putTime(o, "in_time", inTime)
        putTime(o, "close_time", closeTime)
        return o
    }

    /**
     * 去重指纹：**不含 `bathTime`**。
     *
     * 它是累计值，而"累计到多少"不参与状态派生——服务端判断"暂停"只看 `bathTime > 0`，
     * 网页端显示的淋浴时长又是拿 `inTime` 与当前时刻自己算的。放进指纹会让"只有累计值
     * 不同"的两帧被判成变化，白白多传一份内容相同、语义也相同的帧。
     *
     * 依据是 `ntu-bathapp-reverse/notes/2026-06-21-doorlist-schema.md`：浴室 App 在**洗浴期间根本不写库**
     * （`DoorStatusConfirmThread` 每秒轮询，但只在四个超时条件之一满足时才落 SQL）。
     * `bathTime` 仅在用户关阀暂停那一刻写一次，在释放喷头时归零。所以这条**不是**
     * "省掉每秒的流量"——那种流量本来就不存在——只是让指纹只包含真正决定状态的那几项。
     *
     * 用 `data class` 的 `hashCode` 直接比对整行行不通，这也是为什么这里要显式列出参与
     * 比对的字段，而不是写 `this`。
     */
    fun fingerprint(): List<Any> = listOf(doorNo, cardno, certno, accno, openTime, inTime, closeTime, status)

    /**
     * 这个喷头是否有人占用。
     *
     * 与 `server/app/modules/doors/service.py` 的七态判据一致：维修中（1）与已禁用（3）
     * 先排除；其余看 `cardno` 或 `accno`。`certno` 是占用者标识，不参与“有没有人”。
     */
    fun isOccupied(): Boolean = status != STATUS_REPAIR &&
        status != STATUS_DISABLED &&
        (cardno != 0L || (accno.isNotEmpty() && accno != "0"))

    private fun putTime(target: JSONObject, name: String, raw: String) {
        if (raw.isBlank()) return
        val parsed = try {
            localFormat.parse(raw.trim())
        } catch (e: Exception) {
            null
        } ?: return
        target.put(name, Clock.formatRfc3339(parsed))
    }

    companion object {
        private val localFormat = SimpleDateFormat("yyyyMMddHHmmss", Locale.US)

        /** t_doorlist.status 的原始取值：1 = 维修中，3 = 已禁用。 */
        const val STATUS_REPAIR = 1
        const val STATUS_DISABLED = 3

        /** sqlite3 -separator '|' 输出解析：doorNo|cardno|certno|accno|openTime|inTime|closeTime|bathTime|status */
        fun parseRows(output: String): List<DoorRaw> =
            output.lines().filter { it.isNotBlank() }.mapNotNull { parseRow(it) }

        /**
         * 解析一次读取并标出完整性。
         *
         * 没有任何非空行、退出码非 0、任一行解析失败都给 `complete = false`。
         * 空表在设备上说明这次读不可用（t_doorlist 恒有喷头行），不能当成 0 个占用。
         */
        fun parseReading(output: String, exitCode: Int): DoorReading {
            if (exitCode != 0) return DoorReading(emptyList(), complete = false)
            val lines = output.lines().filter { it.isNotBlank() }
            if (lines.isEmpty()) return DoorReading(emptyList(), complete = false)
            val doors = parseRows(output)
            return DoorReading(doors, complete = doors.size == lines.size)
        }

        fun parseRow(line: String): DoorRaw? {
            return try {
                val p = line.split("|")
                if (p.size < 9) {
                    null
                } else {
                    val doorNo = p[0].trim().toIntOrNull() ?: return null
                    val status = p[8].trim().toIntOrNull() ?: return null
                    DoorRaw(
                        doorNo = doorNo,
                        cardno = p[1].trim().toLongOrNull() ?: 0L,
                        certno = p[2],
                        accno = p[3],
                        openTime = p[4],
                        inTime = p[5],
                        closeTime = p[6],
                        bathTime = p[7].trim().toIntOrNull() ?: 0,
                        status = status,
                    )
                }
            } catch (e: Exception) {
                null
            }
        }
    }
}
