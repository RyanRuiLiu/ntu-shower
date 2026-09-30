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
