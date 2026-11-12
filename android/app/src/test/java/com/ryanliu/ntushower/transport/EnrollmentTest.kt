package com.ryanliu.ntushower.transport

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** 设备自注册的纯逻辑：请求形状与服务端契约逐字段对齐，响应解析的失败边界。 */
class EnrollmentTest {

    @Test
    fun `request carries the hello-shaped fields`() {
        val body = Enrollment.encodeRequest(
            deviceId = "1c94310a536777d6",
            appVersion = "0.6.5",
            versionCode = 116,
            osVersion = "4.4.4",
            capabilities = setOf("session.assign", "ui.dump"),
        )
        assertEquals("1c94310a536777d6", body.getString("device_id"))
        assertEquals("0.6.5", body.getString("app_version"))
        assertEquals(116, body.getInt("version_code"))
        assertEquals("4.4.4", body.getString("os_version"))
        val caps = body.getJSONArray("capabilities")
        assertEquals(2, caps.length())
        assertTrue(caps.toString().contains("session.assign"))
    }

    @Test
    fun `empty capabilities serialize as an array not a string`() {
        val caps = Enrollment.encodeRequest("d", "", 0, "", emptySet()).getJSONArray("capabilities")
        assertEquals(0, caps.length())
        assertTrue(caps is JSONArray)
    }

    @Test
    fun `token is extracted from the enroll response`() {
        assertEquals("abc-token", Enrollment.parseToken("""{"device_id":"d","token":"abc-token"}"""))
    }

    @Test
    fun `a blank or missing token is a failure not an empty credential`() {
        // 空令牌落盘会让设备绕过重新注册的自愈路径——必须按失败处理
        assertNull(Enrollment.parseToken("""{"device_id":"d","token":""}"""))
        assertNull(Enrollment.parseToken("""{"device_id":"d"}"""))
    }

    @Test
    fun `unparseable bodies are failures`() {
        assertNull(Enrollment.parseToken("not json"))
        assertNull(Enrollment.parseToken("<html>gateway error</html>"))
    }

    @Test
    fun `policy violation close code triggers claim re-enrollment`() {
        // RFC 6455 1008：服务端按策略拒绝（凭据无效/缺失），必须清凭据重新自注册
        assertTrue(Enrollment.shouldReEnroll(Enrollment.WS_CLOSE_POLICY_VIOLATION))
    }

    @Test
    fun `ordinary close codes only reconnect without re-enrolling`() {
        // 正常关闭、端点离开、网络异常、内部错误：一律走普通重连，清掉有效凭据反而多余
        listOf(1000, 1001, 1006, 1011, 4000).forEach { code ->
            assertFalse("close=$code should not re-enroll", Enrollment.shouldReEnroll(code))
        }
    }
}
