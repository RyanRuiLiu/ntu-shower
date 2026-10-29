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
     * 必填参数直接用 `optInt` 取：它们在服务端的请求模型里就是必填，缺了根本发不下来，设备端
     * 再判一次只是把同一个结论写两遍。可空参数（`to_x` / `to_y`）必须用 `has()` 判——`optInt`
     * 的默认值是 0，而 0 是合法坐标，与"未提供"分不开。
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

        // 通道是必填项：缺了或值不认识，命令在 executeUiAction 里被明确拒绝，不做静默回落
        SupportedActions.UI_INPUT ->
            CommandScheduler.Command.UiInput(commandId, params.optString("text"), params.optString("channel"))

        SupportedActions.UI_SCROLL -> CommandScheduler.Command.UiScroll(commandId, params.optString("direction"))

        SupportedActions.VOICE_PLAY -> CommandScheduler.Command.PlayVoice(commandId, params)

        SupportedActions.VOICE_PREPARE -> CommandScheduler.Command.PrepareVoice(commandId, params)

        else -> waterCommand(action, params, commandId)
    }

    /**
     * 水控板动作单独成组：服务端只给逻辑门号；板/板内门号由设备端 waterList 解析。
     * 从主 `when` 里拆出来只为让每个函数的复杂度保持在阈值内，分支本身没有变化。
     */
    private fun waterCommand(action: String, params: JSONObject, commandId: String): CommandScheduler.Command? =
        when (action) {
            SupportedActions.WATER_OPEN -> CommandScheduler.Command.WaterOpen(commandId, params.optInt("door_no"))
            SupportedActions.WATER_CLOSE -> CommandScheduler.Command.WaterClose(commandId, params.optInt("door_no"))
            SupportedActions.WATER_STATUS -> CommandScheduler.Command.WaterStatus(commandId)
            else -> null
        }

    /** 取一个可空整数：键不存在返回 null，存在则返回值（含 0）。 */
    private fun JSONObject.optIntOrNull(key: String): Int? = if (has(key)) optInt(key) else null

    /**
     * 滚动方向在协议里的取值。方向是必填的闭集，执行器（[CommandScheduler.executeScroll]）
     * 按这两个常量分派——字面量只在这里出现一次。
     */
    const val DIRECTION_FORWARD = "forward"
    const val DIRECTION_BACKWARD = "backward"

    /** 滑动默认时长（毫秒）。太短会被识别成快速抛掷而非拖动。 */
    private const val DEFAULT_SWIPE_MS = 300
}
