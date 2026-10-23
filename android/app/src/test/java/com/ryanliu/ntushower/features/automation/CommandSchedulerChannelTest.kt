package com.ryanliu.ntushower.features.automation

import com.ryanliu.ntushower.transport.SupportedActions
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 设备端命令队列归属与服务端 `ACTION_CHANNELS` 通道划分的逐动作对账。
 *
 * 动作名两端有 `test_action_names` 对账，但那只钉"名字相同"；这里钉"落哪条队列"。
 * 服务端通道表改了而设备路由没跟上时，表现为设备把命令排进错误的串行链——
 * 与别的命令互相堵住、或本该互斥的并发执行，都不报错。改任何一侧，两侧同步。
 */
class CommandSchedulerChannelTest {

    private fun create(action: String): CommandScheduler.Command? =
        CommandFactory.create(action, JSONObject(), COMMAND_ID)

    /** 镜像服务端 `ACTION_CHANNELS`（core/actions.py）：不在此表的即维护类，不排队。 */
    private val expected: Map<String, CommandChannel> = mapOf(
        SupportedActions.SESSION_ASSIGN to CommandChannel.AUTOMATION,
        SupportedActions.SESSION_SETTLE to CommandChannel.AUTOMATION,
        SupportedActions.UI_TOUCH to CommandChannel.AUTOMATION,
        SupportedActions.UI_SCROLL to CommandChannel.AUTOMATION,
        SupportedActions.UI_KEY to CommandChannel.AUTOMATION,
        SupportedActions.UI_INPUT to CommandChannel.AUTOMATION,
        SupportedActions.UI_DUMP to CommandChannel.AUTOMATION,
        SupportedActions.CAMERA_CAPTURE to CommandChannel.CAPTURE,
        SupportedActions.SCREEN_CAPTURE to CommandChannel.CAPTURE,
        SupportedActions.VOICE_PLAY to CommandChannel.VOICE,
        SupportedActions.VOICE_PREPARE to CommandChannel.VOICE,
    )

    /** 服务端归入 maintenance 通道的动作：设备端不进任何队列，即时执行。 */
    private val maintenance = setOf(
        SupportedActions.DEVICE_EXEC,
        SupportedActions.DEVICE_REBOOT,
        SupportedActions.APP_RESTART,
        SupportedActions.BATH_RESTART,
        SupportedActions.CLOCK_SET,
        SupportedActions.POWER_SCHEDULE,
        SupportedActions.FREELIST_READ,
        SupportedActions.FREELIST_WRITE,
        SupportedActions.BRIDGE_OPEN,
        SupportedActions.BRIDGE_CLOSE,
    )

    @Test
    fun `queued actions route to their channel`() {
        expected.forEach { (action, channel) ->
            val command = create(action)
            assertEquals("动作 $action 应能创建命令", true, command != null)
            assertEquals("动作 $action 的队列归属", channel, CommandScheduler.channelOf(command!!))
        }
    }

    @Test
    fun `maintenance actions are not queued at all`() {
        maintenance.forEach { action ->
            assertNull("维护类动作 $action 不应创建排队命令", create(action))
        }
    }

    @Test
    fun `every supported action is accounted for`() {
        // 反查：新增动作只加 SupportedActions 而不落到这张表（也不在维护集合）时会在此失败
        val unaccounted = SupportedActions.ALL - expected.keys - maintenance
        assertEquals("新增动作未登记队列归属：$unaccounted", emptySet<String>(), unaccounted)
    }

    private companion object {
        const val COMMAND_ID = "channel-test"
    }
}
