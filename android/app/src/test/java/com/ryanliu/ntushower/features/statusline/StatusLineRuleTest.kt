package com.ryanliu.ntushower.features.statusline

import com.ryanliu.ntushower.features.overlay.ConditionInputs
import com.ryanliu.ntushower.features.overlay.ConditionSettings
import com.ryanliu.ntushower.features.overlay.LocalMoment
import com.ryanliu.ntushower.features.overlay.OverlayAlign
import com.ryanliu.ntushower.features.overlay.OverlayReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StatusLineRuleTest {

    private val floor = FloorStatus(deviceId = "dev-1f", name = "1F", online = true, free = 3)

    private fun settings(
        enabled: Boolean = true,
        floors: List<FloorStatus> = listOf(floor),
        timeEnabled: Boolean = false,
        occupancyEnabled: Boolean = false,
    ): StatusLineSettings = StatusLineSettings(
        enabled = enabled,
        textSizeSp = 13,
        textColor = "#ffffff",
        colorMode = StatusLineColorMode.BY_STATE,
        availableColor = "#6bc600",
        unavailableColor = "#999999",
        background = true,
        backgroundColor = "#cc0066cc",
        align = OverlayAlign.CENTER,
        centerOffsetDp = 0,
        xDp = 0,
        yDp = 8,
        condition = ConditionSettings(
            timeEnabled = timeEnabled,
            timeStart = "22:00",
            timeEnd = "23:00",
            weekdays = "1,2,3,4,5,6,7",
            occupancyEnabled = occupancyEnabled,
            occupancyMin = 0,
            occupancyMax = 48,
        ),
        floors = floors,
    )

    private fun decide(
        settings: StatusLineSettings,
        hasStatus: Boolean = true,
        stale: Boolean = false,
        moment: LocalMoment? = null,
        occupied: Int? = null,
        coverVisible: Boolean = false,
    ) = StatusLineRule.decide(settings, ConditionInputs(moment, occupied, coverVisible), hasStatus, stale)

    @Test
    fun disabledHidesFirst() {
        val decision = decide(settings(enabled = false), hasStatus = false, stale = true, coverVisible = true)

        assertEquals(OverlayReason.DISABLED, decision.reason)
        assertFalse(decision.visible)
    }

    @Test
    fun missingOrEmptyStatusHides() {
        assertEquals(StatusLineReason.NO_STATUS, decide(settings(), hasStatus = false).reason)
        assertEquals(StatusLineReason.NO_STATUS, decide(settings(floors = emptyList())).reason)
    }

    @Test
    fun staleHidesBeforeConditions() {
        assertEquals(StatusLineReason.STALE, decide(settings(timeEnabled = true), stale = true).reason)
    }

    @Test
    fun coverAndOccupancyUseTheSharedRule() {
        assertEquals(OverlayReason.COVER_VISIBLE, decide(settings(occupancyEnabled = true), coverVisible = true).reason)
        assertEquals(
            OverlayReason.OCCUPANCY_UNAVAILABLE,
            decide(settings(occupancyEnabled = true), occupied = null).reason,
        )
        assertTrue(decide(settings(occupancyEnabled = true), occupied = 1).visible)
    }

    @Test
    fun timeUsesTheSharedRule() {
        assertEquals(OverlayReason.TIME_UNAVAILABLE, decide(settings(timeEnabled = true), moment = null).reason)
        assertTrue(decide(settings()).visible)
    }
}
