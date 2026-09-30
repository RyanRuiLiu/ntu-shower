package com.ryanliu.ntushower.features.automation

import com.ryanliu.ntushower.transport.SupportedActions
import org.json.JSONObject

/**
 * action + params → [CommandScheduler.Command] 的映射。
 *
 * 独立于 [com.ryanliu.ntushower.service.AgentService]：那是传输层的宿主，
 * 负责收帧、去重、回报；"某个 action 对应哪条命令、参数怎么取"是纯粹的数据
 * 转换，放在一起会让传输层承担它不需要知道的协议细节。
 *
 * 只处理入队执行的命令。设备维护类（重启/卸载/执行）即时执行，见
 * [com.ryanliu.ntushower.service.DeviceMaintenance]。
 */
object CommandFactory {

    /**
     * 解析一条入队执行的命令；不属于此类返回 null。
     *
     * 参数一律用 `has()` 判断再取：`optInt` 的默认值是 0，而 0 是合法坐标与
     * 喷头编号，无法与"未提供"区分。
     */
    fun create(action: String, params: JSONObject, commandId: String): CommandScheduler.Command? = when (action) {
        SupportedActions.SESSION_ASSIGN -> CommandScheduler.Command.Assign(
            id = commandId,
            doorNo = params.optInt("door_no"),
            certNo = params.optString("cert_no"),
            password = params.optString("password"),
        )

        SupportedActions.SESSION_SETTLE -> CommandScheduler.Command.Settle(
            id = commandId,
            certNo = params.optString("cert_no"),
            password = params.optString("password"),
        )

        SupportedActions.CAMERA_CAPTURE -> CommandScheduler.Command.Photo(commandId)

        SupportedActions.SCREEN_CAPTURE -> CommandScheduler.Command.Screen(commandId)

        SupportedActions.UI_DUMP -> CommandScheduler.Command.UiDump(commandId)

        SupportedActions.UI_TOUCH -> CommandScheduler.Command.UiTouch(
            id = commandId,
            x = params.optInt("x"),
            y = params.optInt("y"),
            toX = params.optIntOrNull("to_x"),
            toY = params.optIntOrNull("to_y"),
            durationMs = params.optInt("duration_ms", DEFAULT_SWIPE_MS),
        )

        SupportedActions.UI_KEY -> CommandScheduler.Command.UiKey(commandId, params.optString("key"))

        SupportedActions.UI_INPUT -> CommandScheduler.Command.UiInput(commandId, params.optString("text"))

        SupportedActions.UI_SCROLL -> CommandScheduler.Command.UiScroll(
            id = commandId,
            forward = params.optString("direction", DIRECTION_FORWARD) != DIRECTION_BACKWARD,
        )

        else -> null
    }

    /** 取一个可空整数：键不存在返回 null，存在则返回值（含 0）。 */
    private fun JSONObject.optIntOrNull(key: String): Int? = if (has(key)) optInt(key) else null

    /** 滚动方向在协议里的取值；缺省按 forward（更常用的一侧）。 */
    private const val DIRECTION_FORWARD = "forward"
    private const val DIRECTION_BACKWARD = "backward"

    /** 滑动默认时长（毫秒）。太短会被识别成快速抛掷而非拖动。 */
    private const val DEFAULT_SWIPE_MS = 300
}
