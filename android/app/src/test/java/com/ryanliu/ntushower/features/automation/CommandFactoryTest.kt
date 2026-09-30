package com.ryanliu.ntushower.features.automation

import com.ryanliu.ntushower.transport.SupportedActions
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * action → Command 的映射。
 *
 * 这是服务端与设备端之间唯一的一层"协议参数怎么落到命令上"，参数取错
 * 会让命令带着空值执行（例如喷头编号恒为 0），因此逐个字段都钉住。
 */
class CommandFactoryTest {

    private fun create(action: String, json: String = "{}"): CommandScheduler.Command? =
        CommandFactory.create(action, JSONObject(json), COMMAND_ID)

    @Test
    fun `assign carries door number and credentials`() {
        val command = create(
            SupportedActions.SESSION_ASSIGN,
            """{"door_no":3,"cert_no":"B12345678","password":"secret"}""",
        )

        assertTrue(command is CommandScheduler.Command.Assign)
        val assign = command as CommandScheduler.Command.Assign
        assertEquals(COMMAND_ID, assign.id)
        assertEquals(3, assign.doorNo)
        assertEquals("B12345678", assign.certNo)
        assertEquals("secret", assign.password)
    }

    @Test
    fun `settle carries credentials without door number`() {
        val command = create(SupportedActions.SESSION_SETTLE, """{"cert_no":"B1","password":"p"}""")

        assertTrue(command is CommandScheduler.Command.Settle)
        val settle = command as CommandScheduler.Command.Settle
        assertEquals("B1", settle.certNo)
        assertEquals("p", settle.password)
    }

    @Test
    fun `photo screen and tree take no parameters`() {
        assertTrue(create(SupportedActions.CAMERA_CAPTURE) is CommandScheduler.Command.Photo)
        assertTrue(create(SupportedActions.SCREEN_CAPTURE) is CommandScheduler.Command.Screen)
        assertTrue(create(SupportedActions.UI_DUMP) is CommandScheduler.Command.UiDump)
    }

    @Test
    fun `touch by coordinates`() {
        val command = create(SupportedActions.UI_TOUCH, """{"x":100,"y":200}""")

        assertTrue(command is CommandScheduler.Command.UiTouch)
        val touch = command as CommandScheduler.Command.UiTouch
        assertEquals(100, touch.x)
        assertEquals(200, touch.y)
        assertNull(touch.toX)
        assertNull(touch.toY)
    }

    @Test
    fun `touch with a destination becomes a swipe`() {
        val command = create(SupportedActions.UI_TOUCH, """{"x":10,"y":20,"to_x":30,"to_y":40,"duration_ms":500}""")
        val touch = command as CommandScheduler.Command.UiTouch

        assertEquals(30, touch.toX)
        assertEquals(40, touch.toY)
        assertEquals(500, touch.durationMs)
    }

    @Test
    fun `touch keeps zero as a real coordinate`() {
        // 0 是合法坐标。若用 optInt 的默认值来判断"有没有传"，
        // 原点会被当成"未提供"，点击落到无意义的位置
        val touch = create(SupportedActions.UI_TOUCH, """{"x":0,"y":0}""") as CommandScheduler.Command.UiTouch

        assertEquals(0, touch.x)
        assertEquals(0, touch.y)
    }

    @Test
    fun `touch without coordinates yields zeros for the caller to reject`() {
        // 这里不报错：参数校验属于路由层（它知道该回什么 problem），
        // 工厂只负责如实转换
        val touch = create(SupportedActions.UI_TOUCH) as CommandScheduler.Command.UiTouch

        assertEquals(0, touch.x)
        assertEquals(0, touch.y)
    }

    @Test
    fun `key and text carry their payload`() {
        val key = create(SupportedActions.UI_KEY, """{"key":"home"}""")
        assertEquals("home", (key as CommandScheduler.Command.UiKey).key)

        val text = create(SupportedActions.UI_INPUT, """{"text":"hello"}""")
        assertEquals("hello", (text as CommandScheduler.Command.UiInput).text)
    }

    @Test
    fun `device maintenance actions are not queued commands`() {
        // 它们即时执行，见 DeviceMaintenance；工厂如实返回 null
        assertNull(create(SupportedActions.DEVICE_EXEC))
        assertNull(create(SupportedActions.DEVICE_RESTART))
        assertNull(create(SupportedActions.DEVICE_UNINSTALL))
        assertNull(create(SupportedActions.BRIDGE_OPEN))
        assertNull(create(SupportedActions.BRIDGE_CLOSE))
    }

    @Test
    fun `unknown action yields null`() {
        assertNull(create("nonexistent.action"))
    }

    @Test
    fun `every queued action is reachable from the factory`() {
        // 反查：设备声明支持但工厂不认识的 action 会让命令永远无法执行
        val queued = SupportedActions.ALL - setOf(
            SupportedActions.DEVICE_EXEC,
            SupportedActions.DEVICE_RESTART,
            SupportedActions.DEVICE_UNINSTALL,
            SupportedActions.BRIDGE_OPEN,
            SupportedActions.BRIDGE_CLOSE,
        )
        queued.forEach { action ->
            assertTrue("工厂未处理 $action", create(action) != null)
        }
    }

    private companion object {
        const val COMMAND_ID = "cmd-1"
    }
}
