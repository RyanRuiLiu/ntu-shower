package com.ryanliu.ntushower.transport

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 消息类型登记表的对账。
 *
 * 取值在这里**写成字面量集合**而不引用常量：常量本身改错时，跟着它一起错的测试什么都
 * 发现不了（与 BathAppPageTest 对资源 ID 的处理同理）。反射遍历那半边守的是另一件事：
 * 新增常量忘了放进 [MessageTypes.ALL]，服务端就不认识这条消息。
 */
class MessageTypesTest {

    @Test
    fun `all covers every constant`() {
        val constants = MessageTypes::class.java.declaredFields
            .filter { java.lang.reflect.Modifier.isStatic(it.modifiers) && it.type == String::class.java }
            .map {
                it.isAccessible = true
                it.get(null) as String
            }

        assertEquals(constants.toSet(), MessageTypes.ALL)
        assertEquals("常量值出现重复", constants.size, constants.toSet().size)
    }

    @Test
    fun `values are pinned verbatim`() {
        assertEquals(
            setOf(
                "link.hello", "link.hello_ack", "link.ping", "link.pong", "link.error",
                "door.snapshot", "door.snapshot_ack",
                "device.command", "device.command_result",
                "config.update", "config.ack",
                "status_line.update",
                "bridge.open", "bridge.close",
                "ota.upgrade_available", "ota.progress",
                "device.log_upload",
            ),
            MessageTypes.ALL,
        )
    }

    @Test
    fun `values use the family dot event form`() {
        MessageTypes.ALL.forEach { type ->
            assertTrue("不符合 <族>.<事件>：$type", type.matches(Regex("""^[a-z][a-z_]*\.[a-z0-9_]+$""")))
        }
    }
}
