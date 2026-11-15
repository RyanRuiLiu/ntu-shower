package com.ryanliu.ntushower.transport

import com.ryanliu.ntushower.BuildConfig
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 自注册 claim 令牌的形状钉子（构建期注入，BuildConfig 是它在 APK 里的最终形态）。
 *
 * 只允许两种取值：**空**（注册通道干净关闭，fail closed）或 **≥32 位 URL-safe
 * 随机串**（服务端 core.tokens 同形）。历史事故：gradle.properties 里曾放占位
 * 中文文本——APK 烧进一个必然被服务端拒绝的废串，设备凭据丢失时自注册必然失败
 * 且两侧都难对账。这道钉子让占位文本在构建测试阶段就红，而不是在设备现场。
 */
class EnrollTokenTest {

    @Test
    fun enrollTokenIsEmptyOrAValidClaim() {
        val token = BuildConfig.DEVICE_ENROLL_TOKEN
        assertTrue(
            "claim 令牌必须是空（注册通道关闭）或 ≥32 位 URL-safe 随机串；" +
                "占位文本/中文/空白会让自注册必然失败且难对账（长度=${token.length}）",
            token.isEmpty() || Regex("[A-Za-z0-9_-]{32,}").matches(token),
        )
    }
}
