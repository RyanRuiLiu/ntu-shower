package com.ryanliu.ntushower.features.voice

import org.junit.Assert.assertEquals
import org.junit.Test

class BathAudioGateTest {
    @Test
    fun `waits through start grace when bath audio is absent`() {
        val gate = BathAudioGate(startGraceMs = 1_500, maxWaitMs = 15_000)

        assertEquals(BathAudioStep.WAIT, gate.next(0, musicActive = false))
        assertEquals(BathAudioStep.WAIT, gate.next(1_499, musicActive = false))
        assertEquals(BathAudioStep.PLAY, gate.next(1_500, musicActive = false))
    }

    @Test
    fun `waits until bath audio ends after seeing it`() {
        val gate = BathAudioGate(startGraceMs = 1_500, maxWaitMs = 15_000)

        assertEquals(BathAudioStep.WAIT, gate.next(0, musicActive = true))
        assertEquals(BathAudioStep.WAIT, gate.next(2_000, musicActive = true))
        assertEquals(BathAudioStep.PLAY, gate.next(2_600, musicActive = false))
    }

    @Test
    fun `sees bath audio start inside the grace`() {
        val gate = BathAudioGate(startGraceMs = 1_500, maxWaitMs = 15_000)

        assertEquals(BathAudioStep.WAIT, gate.next(0, musicActive = false))
        assertEquals(BathAudioStep.WAIT, gate.next(500, musicActive = true))
        assertEquals(BathAudioStep.PLAY, gate.next(2_500, musicActive = false))
    }

    @Test
    fun `times out while bath audio is still active`() {
        val gate = BathAudioGate(startGraceMs = 1_500, maxWaitMs = 15_000)

        assertEquals(BathAudioStep.WAIT, gate.next(0, musicActive = true))
        assertEquals(BathAudioStep.WAIT, gate.next(14_999, musicActive = true))
        assertEquals(BathAudioStep.TIMEOUT, gate.next(15_000, musicActive = true))
    }

    @Test
    fun `plays at deadline when bath audio stopped just before it`() {
        val gate = BathAudioGate(startGraceMs = 1_500, maxWaitMs = 15_000)

        assertEquals(BathAudioStep.WAIT, gate.next(0, musicActive = true))
        assertEquals(BathAudioStep.PLAY, gate.next(15_000, musicActive = false))
    }

    @Test
    fun `plays when max wait is shorter than the start grace and audio is absent`() {
        val gate = BathAudioGate(startGraceMs = 5_000, maxWaitMs = 1_000)

        assertEquals(BathAudioStep.WAIT, gate.next(0, musicActive = false))
        assertEquals(BathAudioStep.PLAY, gate.next(1_000, musicActive = false))
    }
}
