package com.ryanliu.ntushower.features.doors

import com.ryanliu.ntushower.core.clock.Clock
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale

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
     * 不同"的两帧被判成变化，白白多传一份内容相同、语义也相同的快照。
     *
     * 依据是 `ntu-remote-shower/dbGUIDE.md`：浴室 App 在**洗浴期间根本不写库**
     * （`DoorStatusConfirmThread` 每秒轮询，但只在四个超时条件之一满足时才落 SQL）。
     * `bathTime` 仅在用户关阀暂停那一刻写一次，在释放喷头时归零。所以这条**不是**
     * "省掉每秒的流量"——那种流量本来就不存在——只是让指纹只包含真正决定状态的那几项。
     *
     * 用 `data class` 的 `hashCode` 直接比对整行行不通，这也是为什么这里要显式列出参与
     * 比对的字段，而不是写 `this`。
     */
    fun fingerprint(): List<Any> = listOf(doorNo, cardno, certno, accno, openTime, inTime, closeTime, status)

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
