package com.ryanliu.ntushower.features.water

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import org.w3c.dom.Document
import org.w3c.dom.Element
import java.io.ByteArrayInputStream
import javax.xml.parsers.DocumentBuilderFactory

/**
 * 从浴室 App 的 `data.xml` 解析水控板拓扑。
 *
 * ## 唯一的映射来源
 *
 * `waterList` 是浴室/水控服务器下发给 App 的那份列表，App 自己用它创建
 * `BathParams.waterControlList` 与 `t_doorlist`。它的字段：
 *
 * - `waterip` / `waterport`：板子 TCP 地址；
 * - `startnumber` / `nozzlenumber`：该板负责的逻辑喷头区间
 *   `[start, start+count-1]`，板内门号 = `逻辑门号 - start + 1`；
 * - `watercode` / `watername`：板子编码与名称（只用于展示/日志）。
 *
 * 设备级协议 POS 取同一文件的 `BathControlPosno`（六台实测都是 1）。
 *
 * ## 明确不读
 *
 * - `BathControlIP`（192.168.16.222）：旧默认值，不是 waterList；
 * - `DoorCount`（48）：旧默认值；
 * - `t_doorlist.waterCtrl*`：App 派生列，2F 是零，不能当主数据源；
 * - `F4 B1` 的 doorNum：板子本地配置（6F 是 24），不是本机使用的门数。
 *
 * 读文件失败/格式坏/门号不在任何区间都会返回空/None，调用方报
 * `water.target_not_found`；不回落旧默认值、不猜板子。
 */
object WaterTargetResolver {

    private const val TAG = "WaterTargetResolver"

    data class Board(
        val code: String,
        val name: String,
        val ip: String,
        val port: Int,
        val pos: Int,
        val start: Int,
        val count: Int,
    ) {
        fun contains(doorNo: Int): Boolean = doorNo in start until (start + count)
    }

    data class Target(val board: Board, val doorNo: Int, val boardDoor: Int)

    /** 纯解析：输入 `data.xml` 原文，输出 waterList 里有效的板。单元测试直接调它。 */
    fun parse(xml: String): List<Board> {
        if (xml.isBlank()) return emptyList()
        val doc = try {
            DocumentBuilderFactory.newInstance().newDocumentBuilder()
                .parse(ByteArrayInputStream(xml.toByteArray(Charsets.UTF_8)))
        } catch (e: Exception) {
            Log.w(TAG, "解析 data.xml 失败：${e.message}")
            return emptyList()
        }
        val (waterList, posText) = readPrefs(doc)
        val pos = posText?.trim()?.toIntOrNull() ?: return emptyList()
        val json = waterList?.takeIf { it.isNotBlank() } ?: return emptyList()
        return try {
            val array = JSONArray(json)
            val boards = ArrayList<Board>(array.length())
            for (index in 0 until array.length()) {
                val board = parseBoard(array.getJSONObject(index), pos) ?: continue
                boards.add(board)
            }
            boards
        } catch (e: Exception) {
            // 不打印 XML 原文：同一文件里有 usercode/seedkey，它们与开阀无关。
            Log.w(TAG, "解析 waterList 失败：${e.message}")
            emptyList()
        }
    }

    /** 读 `<string name="waterList">` 与 `<string name="BathControlPosno">` 的原文。 */
    private fun readPrefs(doc: Document): Pair<String?, String?> {
        var waterList: String? = null
        var posText: String? = null
        val strings = doc.getElementsByTagName("string")
        for (index in 0 until strings.length) {
            val element = strings.item(index) as Element
            when (element.getAttribute("name")) {
                "waterList" -> waterList = element.textContent
                "BathControlPosno" -> posText = element.textContent
            }
        }
        return waterList to posText
    }

    /** 一条 waterList 记录 → 板；任一必填字段缺失/越界返回 null（该条不参与控制）。 */
    private fun parseBoard(obj: JSONObject, pos: Int): Board? {
        val ip = obj.optString("waterip").trim()
        if (ip.isEmpty()) return null
        val port = obj.optString("waterport").trim().toIntOrNull() ?: return null
        if (port !in 1..65535) return null
        val start = obj.optString("startnumber").trim().toIntOrNull() ?: return null
        if (start < 1) return null
        val count = obj.optString("nozzlenumber").trim().toIntOrNull() ?: return null
        if (count < 1) return null
        return Board(
            code = obj.optString("watercode").trim(),
            name = obj.optString("watername").trim(),
            ip = ip,
            port = port,
            pos = pos,
            start = start,
            count = count,
        )
    }

    /** 纯换算版本，供单元测试直接喂 waterList 解析结果。 */
    fun targetIn(boards: List<Board>, doorNo: Int): Target? {
        val board = boards.firstOrNull { it.contains(doorNo) } ?: return null
        return Target(board, doorNo, doorNo - board.start + 1)
    }
}
