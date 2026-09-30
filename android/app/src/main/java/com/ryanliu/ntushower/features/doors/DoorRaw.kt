package com.ryanliu.ntushower.features.doors

import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

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
     * 那个字段在有人洗浴时每秒都在变（浴室 App 每秒写库刷新时长）。把它算进指纹等于没有
     * 去重——占用期间每秒都会判定为"变了"从而上报一整份快照（7 个喷头约 1.2 KB），
     * 而接收方并不需要它：
     *
     * - 服务端存的 `bath_time_s` 只是那一刻的快照值，它按 `in_time` 与当前时刻自己就能算；
     * - 网页端显示的"淋浴时长"是前端拿 `in_time` 本地算的（每秒重算），不靠推送推进。
     *
     * 真正值得上报的时刻是"谁进来了、阀开了、暂停了、结束了、清零了"——那些都体现在其余
     * 字段上。排除它之后，占用期从每秒一次降到每个状态迁移一次。
     *
     * 用 `data class` 的 `hashCode` 直接比对整行是行不通的，这也是为什么这里要显式列出
     * 参与比对的那几项，而不是写 `this`。
     */
    fun fingerprint(): List<Any> = listOf(doorNo, cardno, certno, accno, openTime, inTime, closeTime, status)

    private fun putTime(target: JSONObject, name: String, raw: String) {
        if (raw.isBlank()) return
        val parsed = try {
            localFormat.parse(raw.trim())
        } catch (e: Exception) {
            null
        } ?: return
        target.put(name, utcFormat.format(parsed))
    }

    companion object {
        private val localFormat = SimpleDateFormat("yyyyMMddHHmmss", Locale.US)
        private val utcFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }

        /** sqlite3 -separator '|' 输出解析：doorNo|cardno|certno|accno|openTime|inTime|closeTime|bathTime|status */
        fun parseRows(output: String): List<DoorRaw> =
            output.lines().filter { it.isNotBlank() }.mapNotNull { parseRow(it) }

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
