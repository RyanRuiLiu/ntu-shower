package com.ryanliu.ntushower.features.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceVolumeTest {

    @Test
    fun `changes when configured target differs`() {
        val plan = VoiceVolume.plan(3, 4, 5)

        assertEquals(3, plan.target)
        assertEquals(4, plan.restore)
        assertTrue(plan.change)
    }

    @Test
    fun `does nothing when target equals current`() {
        val plan = VoiceVolume.plan(5, 5, 5)

        assertEquals(5, plan.target)
        assertEquals(5, plan.restore)
        assertFalse(plan.change)
    }

    @Test
    fun `clamps configured and current to stream maximum`() {
        val plan = VoiceVolume.plan(9, 9, 5)

        assertEquals(5, plan.target)
        assertEquals(5, plan.restore)
        assertFalse(plan.change)
    }

    @Test
    fun `clamps negative values to zero`() {
        val plan = VoiceVolume.plan(-1, 4, 5)

        assertEquals(0, plan.target)
        assertEquals(4, plan.restore)
        assertTrue(plan.change)
    }

    @Test
    fun `zero max makes both sides zero`() {
        val plan = VoiceVolume.plan(3, 4, 0)

        assertEquals(0, plan.target)
        assertEquals(0, plan.restore)
        assertFalse(plan.change)
    }
}
