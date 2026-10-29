package com.ryanliu.ntushower.features.water

import android.util.Log
import com.ryanliu.ntushower.features.automation.CommandOutcome
import com.ryanliu.ntushower.features.automation.CommandScheduler
import com.ryanliu.ntushower.transport.OutcomeStatus
import com.ryanliu.ntushower.transport.ReasonCodes
import org.json.JSONArray
import org.json.JSONObject

/**
 * `water.open` / `water.close` / `water.status` 的设备端执行。
 *
 * ## 边界
 *
 * - **不建会话**、不写浴室 App 数据库、不发 `F4 B0`（板配置由 App 自己设）；
 * - 板坐标/门数来自 [WaterTargetResolver]（`data.xml` 的 waterList），不是服务端下发；
 * - 板配置 `F4 B1` 只进 `data.boards[].config` 供页面展示，不参与控制；
 * - 门数以浴室服务器下发的 waterList 为准；5F/6F 的板子会自报 N=24，但现场
 *   实际只有 7 个喷头，后 17 条**不返回、不展示**。
 *
 * ## 物理/协议位义（实测）
 *
 * 现场实测（见 `docs/DECISIONS.md`「水控板现场实测」）：
 * `0x01` 授权位、`0x02` 按钮位、`0x04` 出水位；`0x07` 三者、
 * `0x03` 授权+按钮（未出水）、`0x00` 关闭。反编译文档的 0x02/0x04 位名与现场相反。
 * `water.open` 只置授权位，**不直接出水**；真正出水仍需现场按电磁阀。
 * 这里原样返回 status，页面负责翻译。
 */
object WaterHandler {

    private const val TAG = "WaterHandler"

    /**
     * 执行一条水控板命令。
     *
     * 三个依赖参数带生产默认值：调度器调用 `execute(command)` 即可；单测传入固定的
     * 映射/板子回包，不需要 root shell 或真实板子。默认值只在**省略对应参数时**求值。
     */
    internal fun execute(
        command: CommandScheduler.Command,
        resolveTarget: (Int) -> WaterTargetResolver.Target? = { WaterTargetReader.target(it) },
        listBoards: () -> List<WaterTargetResolver.Board> = { WaterTargetReader.boards() },
        board: WaterBoard = WaterBoardClient(),
    ): CommandOutcome = when (command) {
        is CommandScheduler.Command.WaterOpen -> switch(command.id, command.doorNo, open = true, resolveTarget, board)
        is CommandScheduler.Command.WaterClose -> switch(command.id, command.doorNo, open = false, resolveTarget, board)
        is CommandScheduler.Command.WaterStatus -> status(command.id, listBoards, board)
        else -> failure(command.id, ReasonCodes.PROTO_UNKNOWN_ACTION, "不是水控板动作")
    }

    private fun switch(
        commandId: String,
        doorNo: Int,
        open: Boolean,
        resolveTarget: (Int) -> WaterTargetResolver.Target?,
        client: WaterBoard,
    ): CommandOutcome {
        val target = resolveTarget(doorNo)
            ?: return failure(
                commandId,
                ReasonCodes.WATER_TARGET_NOT_FOUND,
                "喷头 $doorNo 不在 waterList 的映射范围内",
            )
        return try {
            val response = if (open) {
                client.open(target.board, target.boardDoor)
            } else {
                client.close(target.board, target.boardDoor)
            }
            if (!response.accepted) {
                return failure(
                    commandId,
                    ReasonCodes.WATER_BOARD_REJECTED,
                    "水控板拒绝${if (open) "授权出水" else "断水"}：return=${response.returnCode}",
                    target,
                    response.raw,
                )
            }
            val data = JSONObject()
                .put("door_no", doorNo)
                .put("board_door", target.boardDoor)
                .put("board", boardJson(target.board))
                .put("ack", true)
                .put("raw", response.raw.toHex())
            val message = if (open) "已授权出水" else "已断水"
            CommandOutcome(commandId, OutcomeStatus.OK, "", message, data)
        } catch (e: WaterBoardException) {
            Log.w(TAG, "水控板${if (open) "授权" else "断水"}失败：${e.message}")
            failure(commandId, e.reasonCode, e.message ?: "水控板请求失败", target)
        }
    }

    private fun status(
        commandId: String,
        listBoards: () -> List<WaterTargetResolver.Board>,
        client: WaterBoard,
    ): CommandOutcome {
        val boards = listBoards()
        if (boards.isEmpty()) {
            return failure(commandId, ReasonCodes.WATER_TARGET_NOT_FOUND, "设备 waterList 为空或 data.xml 不可读")
        }
        val result = JSONArray()
        return try {
            for (board in boards) {
                val config = client.config(board)
                if (config.returnCode != 0) {
                    return failure(
                        commandId,
                        ReasonCodes.WATER_BOARD_REJECTED,
                        "读水控板配置被拒绝：return=${config.returnCode}",
                    )
                }
                val doorNum = config.doorNum
                val bathMin = config.bathMinutes
                if (doorNum == null || bathMin == null) {
                    return failure(
                        commandId,
                        ReasonCodes.WATER_FRAME_INVALID,
                        "板配置回包缺少门数或最长洗浴时长",
                    )
                }
                val status = client.status(board)
                if (status.returnCode != 0) {
                    return failure(
                        commandId,
                        ReasonCodes.WATER_BOARD_REJECTED,
                        "读水控板状态被拒绝：return=${status.returnCode}",
                    )
                }
                if (status.doors.size < board.count) {
                    return failure(
                        commandId,
                        ReasonCodes.WATER_FRAME_INVALID,
                        "水控板只回报 ${status.doors.size} 门，waterList 需要 ${board.count} 门",
                    )
                }
                val doors = JSONArray()
                // 门数以浴室服务器下发的 waterList 为准。5F/6F 的板子会自报 24 门，
                // 但现场实际只有 7 个喷头；板报多出的记录不当作物理喷头。
                status.doors.take(board.count).forEach { door ->
                    doors.put(
                        JSONObject()
                            .put("door_no", board.start + door.boardDoor - 1)
                            .put("board_door", door.boardDoor)
                            .put("status", door.status)
                            .put("bath_s", door.bathSeconds)
                            .put("allopen_s", door.allOpenSeconds),
                    )
                }
                result.put(
                    boardJson(board)
                        .put(
                            "config",
                            JSONObject()
                                .put("door_num", doorNum)
                                .put("bath_min", bathMin),
                        )
                        .put("doors", doors)
                        .put(
                            "raw",
                            JSONObject()
                                .put("config", config.raw.toHex())
                                .put("status", status.raw.toHex()),
                        ),
                )
            }
            CommandOutcome(
                commandId,
                OutcomeStatus.OK,
                "",
                "",
                JSONObject().put("boards", result),
            )
        } catch (e: WaterBoardException) {
            Log.w(TAG, "读水控板信息失败：${e.message}")
            failure(commandId, e.reasonCode, e.message ?: "水控板请求失败")
        }
    }

    private fun boardJson(board: WaterTargetResolver.Board): JSONObject = JSONObject()
        .put("code", board.code)
        .put("name", board.name)
        .put("ip", board.ip)
        .put("port", board.port)
        .put("pos", board.pos)
        .put("start", board.start)
        .put("count", board.count)

    private fun failure(
        commandId: String,
        reasonCode: String,
        message: String,
        target: WaterTargetResolver.Target? = null,
        raw: ByteArray? = null,
    ): CommandOutcome {
        val data = JSONObject()
        if (target != null) {
            data.put("door_no", target.doorNo)
                .put("board_door", target.boardDoor)
                .put("board", boardJson(target.board))
        }
        if (raw != null) data.put("raw", raw.toHex())
        Log.w(TAG, "water 失败：$reasonCode $message")
        return CommandOutcome(commandId, OutcomeStatus.FAILED, reasonCode, message, data)
    }

    private fun ByteArray.toHex(): String = joinToString("") { "%02X".format(it) }
}
