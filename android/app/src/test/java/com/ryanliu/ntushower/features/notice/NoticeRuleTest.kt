package com.ryanliu.ntushower.features.notice

import com.ryanliu.ntushower.features.overlay.ConditionInputs
import com.ryanliu.ntushower.features.overlay.ConditionSettings
import com.ryanliu.ntushower.features.overlay.OverlayAlign
import com.ryanliu.ntushower.features.overlay.OverlayReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NoticeRuleTest {

    private fun settings(enabled: Boolean = true, text: String = "提示", timeEnabled: Boolean = false): NoticeSettings =
        NoticeSettings(
            enabled = enabled,
            text = text,
            textSizeSp = 48,
            textColor = "#d94f16",
            align = OverlayAlign.LEFT,
            centerOffsetDp = 0,
            xDp = 40,
            yDp = 40,
            condition = ConditionSettings(
                timeEnabled = timeEnabled,
                timeStart = "22:00",
                timeEnd = "23:00",
                weekdays = "1,2,3,4,5,6,7",
                occupancyEnabled = false,
                occupancyMin = 0,
                occupancyMax = 48,
            ),
        )

    private fun decide(settings: NoticeSettings, coverVisible: Boolean = false) =
        NoticeRule.decide(settings, ConditionInputs(null, null, coverVisible))

    @Test
    fun disabledHidesFirst() {
        val decision = decide(settings(enabled = false, text = ""), coverVisible = true)

        assertEquals(OverlayReason.DISABLED, decision.reason)
        assertFalse(decision.visible)
    }

    @Test
    fun blankTextHidesBeforeCoverAndConditions() {
        val decision = decide(settings(text = "   "), coverVisible = true)

        assertEquals(NoticeReason.EMPTY_TEXT, decision.reason)
    }

    @Test
    fun sharedConditionsAreAppliedAfterNoticeSpecificChecks() {
        val decision = decide(settings(timeEnabled = true))

        assertEquals(OverlayReason.TIME_UNAVAILABLE, decision.reason)
        assertTrue(decide(settings()).visible)
    }
}
