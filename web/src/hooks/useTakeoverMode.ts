'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

export type TakeoverPhase = 'idle' | 'disruption' | 'breach' | 'awakening' | 'hud' | 'active'

export interface TakeoverState {
  active: boolean
  phase:  TakeoverPhase
}

// ── Audio ────────────────────────────────────────────────────────────────────

function ctx(): AudioContext | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const C = window.AudioContext ?? (window as any).webkitAudioContext
    return C ? new C() : null
  } catch { return null }
}

function playGlitchNoise(): void {
  try {
    const ac = ctx(); if (!ac) return
    const dur = 0.22
    const buf = ac.createBuffer(1, Math.floor(ac.sampleRate * dur), ac.sampleRate)
    const d = buf.getChannelData(0)
    for (let i = 0; i < d.length; i++) {
      d[i] = (Math.random() * 2 - 1) * Math.exp(-i / (ac.sampleRate * 0.06)) * 0.35
    }
    const f = ac.createBiquadFilter(); f.type = 'bandpass'; f.frequency.value = 380; f.Q.value = 0.5
    const g = ac.createGain()
    g.gain.setValueAtTime(1, ac.currentTime)
    g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + dur)
    const s = ac.createBufferSource(); s.buffer = buf
    s.connect(f); f.connect(g); g.connect(ac.destination)
    s.start(); s.onended = () => ac.close().catch(() => {})
  } catch { /* ok */ }
}

function playGlassCrack(): void {
  try {
    const ac = ctx(); if (!ac) return
    const dur = 0.35
    const buf = ac.createBuffer(1, Math.floor(ac.sampleRate * dur), ac.sampleRate)
    const d = buf.getChannelData(0)
    for (let i = 0; i < d.length; i++) {
      const t = i / ac.sampleRate
      const env = t < 0.003 ? t / 0.003 : Math.exp(-(t - 0.003) * 18)
      d[i] = (Math.random() * 2 - 1) * env * 0.45
    }
    const f = ac.createBiquadFilter(); f.type = 'highpass'; f.frequency.value = 1800
    const s = ac.createBufferSource(); s.buffer = buf
    s.connect(f); f.connect(ac.destination)
    s.start(); s.onended = () => ac.close().catch(() => {})
  } catch { /* ok */ }
}

function playBassPulse(): void {
  try {
    const ac = ctx(); if (!ac) return
    const osc = ac.createOscillator()
    const g = ac.createGain()
    osc.type = 'sine'
    osc.frequency.setValueAtTime(65, ac.currentTime)
    osc.frequency.exponentialRampToValueAtTime(26, ac.currentTime + 0.9)
    g.gain.setValueAtTime(0.001, ac.currentTime)
    g.gain.linearRampToValueAtTime(0.5, ac.currentTime + 0.05)
    g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + 0.9)
    osc.connect(g); g.connect(ac.destination)
    osc.start(ac.currentTime); osc.stop(ac.currentTime + 0.9)
    osc.onended = () => ac.close().catch(() => {})
  } catch { /* ok */ }
}

function playWhoosh(): void {
  try {
    const ac = ctx(); if (!ac) return
    const dur = 0.65
    const buf = ac.createBuffer(2, Math.floor(ac.sampleRate * dur), ac.sampleRate)
    for (let c = 0; c < 2; c++) {
      const d = buf.getChannelData(c)
      for (let i = 0; i < d.length; i++) {
        const t = i / ac.sampleRate
        const env = Math.min(t / 0.04, 1) * Math.pow(1 - t / dur, 1.7)
        d[i] = (Math.random() * 2 - 1) * env * (c === 0 ? 0.38 : 0.28)
      }
    }
    const f = ac.createBiquadFilter(); f.type = 'bandpass'; f.Q.value = 0.65
    f.frequency.setValueAtTime(1300, ac.currentTime)
    f.frequency.exponentialRampToValueAtTime(110, ac.currentTime + dur)
    const g = ac.createGain()
    g.gain.setValueAtTime(0.85, ac.currentTime)
    g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + dur)
    const s = ac.createBufferSource(); s.buffer = buf
    s.connect(f); f.connect(g); g.connect(ac.destination)
    s.start(); s.onended = () => ac.close().catch(() => {})
  } catch { /* ok */ }
}

function playUILock(delayMs = 0): void {
  setTimeout(() => {
    try {
      const ac = ctx(); if (!ac) return
      const dur = 0.09
      const buf = ac.createBuffer(1, Math.floor(ac.sampleRate * dur), ac.sampleRate)
      const d = buf.getChannelData(0)
      for (let i = 0; i < d.length; i++) {
        const t = i / ac.sampleRate
        d[i] = (Math.random() * 2 - 1) * Math.exp(-t * 75) * 0.55
      }
      const f = ac.createBiquadFilter(); f.type = 'highpass'; f.frequency.value = 900
      const s = ac.createBufferSource(); s.buffer = buf
      s.connect(f); f.connect(ac.destination)
      s.start(); s.onended = () => ac.close().catch(() => {})
    } catch { /* ok */ }
  }, delayMs)
}

async function speakLine(text: string): Promise<void> {
  try {
    const res = await fetch('/api/v1/voice/synthesize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice: 'am_onyx' }),
    })
    if (!res.ok) return
    const blob = await res.blob()
    const url  = URL.createObjectURL(blob)
    await new Promise<void>((resolve) => {
      const audio   = new Audio(url)
      audio.onended = () => { URL.revokeObjectURL(url); resolve() }
      audio.onerror = () => { URL.revokeObjectURL(url); resolve() }
      audio.play().catch(resolve)
    })
  } catch { /* ok */ }
}

function openLofi(): void {
  try { window.open('https://www.youtube.com/watch?v=jfKfPfyJRdk', '_blank', 'noopener') } catch { /* ok */ }
}

export function getSystemLine(): string {
  const h = new Date().getHours()
  if (h >= 22 || h < 4)  return 'Late night grind. Neural systems active.'
  if (h < 8)             return 'Early session initialized.'
  if (h < 12)            return 'Morning protocol engaged.'
  if (h < 17)            return 'Afternoon mode. Focus locked.'
  return 'Neural link established.'
}

function sleep(ms: number) { return new Promise<void>((r) => setTimeout(r, ms)) }

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useTakeoverMode() {
  const [state, setState]   = useState<TakeoverState>({ active: false, phase: 'idle' })
  const runningRef           = useRef(false)
  const systemLineRef        = useRef(getSystemLine())

  const startTakeover = useCallback(async () => {
    if (runningRef.current) return
    runningRef.current = true
    systemLineRef.current = getSystemLine()
    console.log('[TAKEOVER] breach started')

    // Phase 1 — Signal disruption (300ms)
    setState({ active: true, phase: 'disruption' })
    playGlitchNoise()
    await sleep(300)

    // Phase 2 — Screen breach (1500ms)
    setState({ active: true, phase: 'breach' })
    playGlassCrack()
    console.log('[TAKEOVER] neural core online')
    await sleep(1500)

    // Phase 3 — Core awakening: wait for backend TTS to finish
    setState({ active: true, phase: 'awakening' })
    playBassPulse()
    playWhoosh()
    await new Promise<void>((resolve) => {
      const handler = () => { window.removeEventListener('xyron:tts-end', handler); resolve() }
      window.addEventListener('xyron:tts-end', handler)
      setTimeout(resolve, 5_000)
    })
    await sleep(700)

    // Phase 4 — HUD deployment
    setState({ active: true, phase: 'hud' })
    console.log('[TAKEOVER] HUD deployed')
    playUILock(0)
    playUILock(160)
    playUILock(300)
    playUILock(450)

    await sleep(500)
    openLofi()
    await sleep(900)
    await speakLine('Workspace ready.')

    setState({ active: true, phase: 'active' })
    console.log('[TAKEOVER] takeover complete')
    runningRef.current = false
  }, [])

  const stopTakeover = useCallback(() => {
    runningRef.current = false
    setState({ active: false, phase: 'idle' })
  }, [])

  useEffect(() => {
    const handler = () => void startTakeover()
    window.addEventListener('xyron:takeover', handler)
    return () => window.removeEventListener('xyron:takeover', handler)
  }, [startTakeover])

  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') stopTakeover() }
    window.addEventListener('keydown', onEsc)
    return () => window.removeEventListener('keydown', onEsc)
  }, [stopTakeover])

  return {
    active:     state.active,
    phase:      state.phase,
    systemLine: systemLineRef.current,
    stopTakeover,
  }
}
