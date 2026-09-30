package com.ryanliu.ntushower.transport

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 能力集合是设备对服务端的自我声明，服务端据此拒绝下发未知命令；
 * 漏登记会让该命令永远发不到设备，多登记则让服务端的拦截失效。
 */
class SupportedActionsTest {

    @Test
    fun `covers every action the agent dispatches`() {
        // 与 AgentService.dispatchCommand 的 when 分支一一对应
        val dispatched = setOf(
            "session.assign",
            "session.settle",
            "photo.capture",
            "screen.capture",
            "app.restart",
            "app.uninstall",
            "debug.exec",
            "bridge.open",
            "bridge.close",
        )

        assertEquals(dispatched, SupportedActions.ALL)
    }

    @Test
    fun `constants match their set entries`() {
        // 常量与集合必须一致：新增常量忘了加进 ALL，服务端就会拒发该命令
        assertTrue(SupportedActions.SESSION_ASSIGN in SupportedActions.ALL)
        assertTrue(SupportedActions.SESSION_SETTLE in SupportedActions.ALL)
        assertTrue(SupportedActions.PHOTO_CAPTURE in SupportedActions.ALL)
        assertTrue(SupportedActions.SCREEN_CAPTURE in SupportedActions.ALL)
        assertTrue(SupportedActions.APP_RESTART in SupportedActions.ALL)
        assertTrue(SupportedActions.APP_UNINSTALL in SupportedActions.ALL)
        assertTrue(SupportedActions.DEBUG_EXEC in SupportedActions.ALL)
        assertTrue(SupportedActions.BRIDGE_OPEN in SupportedActions.ALL)
        assertTrue(SupportedActions.BRIDGE_CLOSE in SupportedActions.ALL)
    }

    @Test
    fun `actions use the domain dot verb form`() {
        // 命名约定：<域>.<动作>，服务端与设备端一致
        SupportedActions.ALL.forEach { action ->
            assertTrue("不符合 <域>.<动作>：$action", action.matches(Regex("""^[a-z]+\.[a-z_]+$""")))
        }
    }
}
