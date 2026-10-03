package com.ryanliu.ntushower.features.automation

import com.ryanliu.ntushower.service.DeviceMaintenance
import com.ryanliu.ntushower.transport.SupportedActions
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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
        assertNull(create(SupportedActions.DEVICE_REBOOT))
        assertNull(create(SupportedActions.APP_RESTART))
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
            SupportedActions.DEVICE_REBOOT,
            SupportedActions.APP_RESTART,
            SupportedActions.BATH_RESTART,
            SupportedActions.CLOCK_SET,
            SupportedActions.BRIDGE_OPEN,
            SupportedActions.BRIDGE_CLOSE,
        )
        queued.forEach { action ->
            assertTrue("工厂未处理 $action", create(action) != null)
        }
    }

    @Test
    fun `every action has a handler on the device`() {
        // 上一个测试只查「工厂认不认」，而这些走的是绕开工厂的独立路径。
        // 这里把它们也钉住：任何一个漏掉，设备收到它都会回 proto.unknown_action——
        // 那说明能力集合（设备对服务端的自我声明）已经和实现脱节了。
        val maintenance = setOf(
            SupportedActions.DEVICE_EXEC,
            SupportedActions.DEVICE_REBOOT,
            SupportedActions.APP_RESTART,
            SupportedActions.BATH_RESTART,
            SupportedActions.CLOCK_SET,
        )
        maintenance.forEach { action ->
            assertTrue("$action 没有维护类处理器", DeviceMaintenance.handles(action))
        }

        // 终结类走独立执行器：它们的动作就是终止本进程，排在别的维护命令后面等于没有执行。
        // 真机上如此丢过一次恢复机会——一串超时的 device.exec 挡在前面，重启一次都没跑。
        val terminating = setOf(SupportedActions.APP_RESTART, SupportedActions.DEVICE_REBOOT)
        maintenance.forEach { action ->
            assertEquals("$action 的终结类判定不对", action in terminating, DeviceMaintenance.isTerminating(action))
        }
        assertFalse("未知动作不该被判成终结类", DeviceMaintenance.isTerminating("nonexistent.action"))

        // bridge.open / bridge.close 是独立的信封类型，由 AgentService 直接分支处理，
        // 不进命令分发；服务端发出即返回，不等答复。
        val bridge = setOf(SupportedActions.BRIDGE_OPEN, SupportedActions.BRIDGE_CLOSE)
        bridge.forEach { action ->
            assertFalse("$action 不该被当成维护命令", DeviceMaintenance.handles(action))
            assertNull("$action 不该入队", create(action))
        }

        // 三条路径合起来必须覆盖全部能力，一个不漏。
        // filter 出来的是 List，要转回 Set 才能与 ALL 比内容
        val queued = SupportedActions.ALL.filter { create(it) != null }.toSet()
        assertEquals(SupportedActions.ALL, queued + maintenance + bridge)
    }

    @Test
    fun `direction constants match the protocol values`() {
        // 执行器按这两个常量分派（CommandScheduler.executeScroll），改值等于改协议；
        // 工厂只透传方向、校验在执行器，所以这里把取值本身钉住
        assertEquals("forward", CommandFactory.DIRECTION_FORWARD)
        assertEquals("backward", CommandFactory.DIRECTION_BACKWARD)
    }

    private companion object {
        const val COMMAND_ID = "cmd-1"
    }
}
