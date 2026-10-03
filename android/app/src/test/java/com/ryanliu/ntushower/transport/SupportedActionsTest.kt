package com.ryanliu.ntushower.transport

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 能力集合是设备对服务端的自我声明，服务端据此拒绝下发未知命令；
 * 漏登记会让该命令永远发不到设备，多登记则让服务端的拦截失效。
 *
 * 这份清单同时是服务端 `app/core/actions.py` 的对账对象——
 * 两边不同语言无法共享代码，靠各自的测试把名字钉住。
 */
class SupportedActionsTest {

    @Test
    fun `covers every action the agent dispatches`() {
        // 与 AgentService.dispatchCommand 的 when 分支一一对应
        val dispatched = setOf(
            "session.assign",
            "session.settle",
            "camera.capture",
            "screen.capture",
            "device.exec",
            "device.reboot",
            "app.restart",
            "bath.restart",
            "clock.set",
            "bridge.open",
            "bridge.close",
            "ui.dump",
            "ui.touch",
            "ui.scroll",
            "ui.key",
            "ui.input",
        )

        assertEquals(dispatched, SupportedActions.ALL)
    }

    @Test
    fun `constants match their set entries`() {
        // 常量与集合必须一致：新增常量忘了加进 ALL，服务端就会拒发该命令
        assertTrue(SupportedActions.SESSION_ASSIGN in SupportedActions.ALL)
        assertTrue(SupportedActions.SESSION_SETTLE in SupportedActions.ALL)
        assertTrue(SupportedActions.CAMERA_CAPTURE in SupportedActions.ALL)
        assertTrue(SupportedActions.SCREEN_CAPTURE in SupportedActions.ALL)
        assertTrue(SupportedActions.DEVICE_EXEC in SupportedActions.ALL)
        assertTrue(SupportedActions.DEVICE_REBOOT in SupportedActions.ALL)
        assertTrue(SupportedActions.APP_RESTART in SupportedActions.ALL)
        assertTrue(SupportedActions.BRIDGE_OPEN in SupportedActions.ALL)
        assertTrue(SupportedActions.BRIDGE_CLOSE in SupportedActions.ALL)
        assertTrue(SupportedActions.UI_DUMP in SupportedActions.ALL)
        assertTrue(SupportedActions.UI_TOUCH in SupportedActions.ALL)
        assertTrue(SupportedActions.UI_SCROLL in SupportedActions.ALL)
        assertTrue(SupportedActions.UI_KEY in SupportedActions.ALL)
        assertTrue(SupportedActions.UI_INPUT in SupportedActions.ALL)
    }

    @Test
    fun `actions use the object dot action form`() {
        // 命名约定：<对象>.<动作>。对象是"被操作的东西"，不是代码模块也不是用途
        SupportedActions.ALL.forEach { action ->
            assertTrue("不符合 <对象>.<动作>：$action", action.matches(Regex("""^[a-z]+\.[a-z_]+$""")))
        }
    }

    @Test
    fun `no action uses a purpose word as its object`() {
        // 曾经有过 debug.exec：debug 是用途不是对象，同一个能力在 HTTP 侧却叫
        // /devices/{id}/exec。这里挡住同类命名再次出现
        val purposes = setOf("debug", "misc", "util", "helper", "temp")
        SupportedActions.ALL.forEach { action ->
            val obj = action.substringBefore('.')
            assertTrue("对象用了用途词：$action", obj !in purposes)
        }
    }
}
