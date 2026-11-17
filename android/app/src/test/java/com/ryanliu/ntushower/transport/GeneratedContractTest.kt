package com.ryanliu.ntushower.transport

import com.ryanliu.ntushower.core.config.ConfigDefaults
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 生成契约的一致性自检。
 *
 * 这三份文件由服务端的 `tools/sync_contracts.py` 生成，内容全是常量——但它们**彼此
 * 之间**还有一层约束：常量必须都进集合、值与名字要对得上、集合不能是空的。生成器
 * 改错时这层约束不会被发现，因为编译照样通过，错的是语义。
 *
 * 用反射遍历而不是逐个手写断言：手写清单会随生成物增长而漏掉新项，而漏掉的那一项
 * 恰好就是没人检查的那一项。
 */
class GeneratedContractTest {

    private fun stringConstants(cls: Class<*>): List<String> = cls.declaredFields
        .filter { java.lang.reflect.Modifier.isStatic(it.modifiers) && it.type == String::class.java }
        .map {
            it.isAccessible = true
            it.get(null) as String
        }

    @Test
    fun `outcome statuses cover every protocol value`() {
        assertEquals(
            setOf(OutcomeStatus.OK, OutcomeStatus.FAILED, OutcomeStatus.TIMEOUT, OutcomeStatus.REJECTED),
            OutcomeStatus.ALL,
        )
        // 协议里就是这四个小写词——改这个等于改跨端契约
        assertEquals(setOf("ok", "failed", "timeout", "rejected"), OutcomeStatus.ALL)
    }

    @Test
    fun `every reason code constant is a dotted lowercase path`() {
        val codes = stringConstants(ReasonCodes::class.java)
        assertTrue("生成物是空的，生成器可能没跑到", codes.size > 10)
        codes.forEach { code ->
            // 段内允许数字：`device.a11y_unavailable` 里的 a11y 就是缩写带数字
            assertTrue("不符合 <前缀>.<小写词> 的形式：$code", code.matches(Regex("""^[a-z]+(\.[a-z0-9_]+)+$""")))
        }
        assertEquals("常量值出现重复", codes.size, codes.toSet().size)
    }

    @Test
    fun `ota version pattern accepts exactly three numeric segments`() {
        // 形状由服务端发布流程铸造（生成物），版本号参与文件名构造——行为在这里钉住，
        // 与生成来源的逐字节对账（test_contract_sync.py）互补
        val pattern = Regex(OtaContract.VERSION_PATTERN)
        assertTrue(pattern.matches("1.2.3"))
        assertFalse(pattern.matches("1.2"))
        assertFalse(pattern.matches("1.2.3.4"))
        assertFalse(pattern.matches("1.2.3-beta"))
    }

    @Test
    fun `config defaults are present and usable`() {
        // 这些值直接进 RuntimeConfig 的初值。任何一个为空都会让设备在**拿到下发的配置
        // 之前**处于「配置缺失」状态——那段时间很短，但正好覆盖进程刚起来的那一瞬。
        assertTrue("提示文字的默认值为空是刻意的（空文案不显示），其余键必须非默认零值", ConfigDefaults.NOTICE_TEXT.isBlank())
        assertTrue("颜色要是十六进制写法", ConfigDefaults.NOTICE_TEXT_COLOR.startsWith("#"))
        assertTrue(ConfigDefaults.POWER_OFF_AT.isNotBlank())
        assertTrue(ConfigDefaults.POWER_ON_AT.isNotBlank())
        assertTrue(ConfigDefaults.NOTICE_TEXT_SIZE_SP > 0)
        assertTrue(ConfigDefaults.STATUS_LINE_TEXT_SIZE_SP > 0)
    }
}
