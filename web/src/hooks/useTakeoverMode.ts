'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

export type TakeoverPhase =
  | 'idle'
  | 'command'
  | 'breach'
  | 'root'
  | 'sync'
  | 'activation'
  | 'hud'
  | 'active'
  | 'standdown'

export interface TakeoverState {
  active:        boolean
  phase:         TakeoverPhase
  controlLevel:  number
  showDirective: boolean
}

// ── TTS singleton — one voice instance at a time ─────────────────────────────
const _tts = { audio: null as HTMLAudioElement | null }

function cancelSpeech(): void {
  if (_tts.audio) {
    console.log('[TTS] playback_cancelled')
    _tts.audio.pause()
    _tts.audio.currentTime = 0
    _tts.audio = null
    console.log('[TTS] queue_cleared')
  }
}

async function speakLine(text: string): Promise<void> {
  cancelSpeech()
  console.log(`[TTS] playback_started: "${text.slice(0, 60)}"`)
  try {
    const res = await fetch('/api/v1/voice/synthesize', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice: 'am_onyx' }),
    })
    if (!res.ok) return
    const url = URL.createObjectURL(await res.blob())
    await new Promise<void>((resolve) => {
      const a = new Audio(url)
      _tts.audio = a
      const cleanup = () => { URL.revokeObjectURL(url); _tts.audio = null }
      a.onended  = () => { cleanup(); console.log('[TTS] playback_finished'); resolve() }
      a.onerror  = () => { cleanup(); resolve() }
      a.play().catch(() => { cleanup(); resolve() })
    })
  } catch { /* ok */ }
}

// ── Ambient drone (module-level singleton) ────────────────────────────────────
const _amb = {
  ctx:     null as AudioContext | null,
  gain:    null as GainNode    | null,
  running: false,
}

function _ambCtx(): AudioContext | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const C = window.AudioContext ?? (window as any).webkitAudioContext
    return C ? new C() : null
  } catch { return null }
}

function startAmbient(): void {
  if (_amb.running) return
  try {
    _amb.ctx  = _ambCtx(); if (!_amb.ctx) return
    const ac  = _amb.ctx
    _amb.gain = ac.createGain()
    _amb.gain.gain.setValueAtTime(0.001, ac.currentTime)
    _amb.gain.gain.linearRampToValueAtTime(0.08, ac.currentTime + 3)
    _amb.gain.connect(ac.destination)

    const sub = ac.createOscillator(); sub.type = 'sine';     sub.frequency.value = 32
    const mid = ac.createOscillator(); mid.type = 'sawtooth'; mid.frequency.value = 76
    const mG  = ac.createGain();       mG.gain.value = 0.22
    const lfo = ac.createOscillator(); lfo.type = 'sine';     lfo.frequency.value = 0.28
    const lG  = ac.createGain();       lG.gain.value = 4

    lfo.connect(lG); lG.connect(sub.frequency)
    sub.connect(_amb.gain)
    mid.connect(mG); mG.connect(_amb.gain)
    sub.start(); mid.start(); lfo.start()
    _amb.running = true
  } catch { /* ok */ }
}

function ambientIntensity(level: number): void {
  if (!_amb.ctx || !_amb.gain) return
  _amb.gain.gain.linearRampToValueAtTime(level, _amb.ctx.currentTime + 0.6)
}

function stopAmbient(): void {
  if (!_amb.ctx || !_amb.gain) return
  try {
    _amb.gain.gain.linearRampToValueAtTime(0.001, _amb.ctx.currentTime + 1.8)
    const ctx = _amb.ctx
    setTimeout(() => ctx.close().catch(() => {}), 2000)
  } catch { /* ok */ }
  _amb.ctx = null; _amb.gain = null; _amb.running = false
}

// ── One-shot sound effects ────────────────────────────────────────────────────

function _ac(): AudioContext | null { return _ambCtx() }

export function playGlitchBurst(): void {
  try {
    const ac = _ac(); if (!ac) return
    const dur = 0.25
    const buf = ac.createBuffer(1, Math.floor(ac.sampleRate * dur), ac.sampleRate)
    const d = buf.getChannelData(0)
    for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * Math.exp(-i / (ac.sampleRate * 0.07)) * 0.4
    const f = ac.createBiquadFilter(); f.type = 'bandpass'; f.frequency.value = 350; f.Q.value = 0.5
    const g = ac.createGain()
    g.gain.setValueAtTime(1, ac.currentTime); g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + dur)
    const s = ac.createBufferSource(); s.buffer = buf; s.connect(f); f.connect(g); g.connect(ac.destination)
    s.start(); s.onended = () => ac.close().catch(() => {})
  } catch { /* ok */ }
}

export function playBassImpact(): void {
  try {
    const ac = _ac(); if (!ac) return
    const osc = ac.createOscillator(); osc.type = 'sine'
    osc.frequency.setValueAtTime(80, ac.currentTime)
    osc.frequency.exponentialRampToValueAtTime(22, ac.currentTime + 0.7)
    const g = ac.createGain()
    g.gain.setValueAtTime(0.001, ac.currentTime)
    g.gain.linearRampToValueAtTime(0.65, ac.currentTime + 0.04)
    g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + 0.7)
    osc.connect(g); g.connect(ac.destination)
    osc.start(); osc.stop(ac.currentTime + 0.72)
    osc.onended = () => ac.close().catch(() => {})
  } catch { /* ok */ }
}

export function playGlassCrack(): void {
  try {
    const ac = _ac(); if (!ac) return
    const dur = 0.4
    const buf = ac.createBuffer(1, Math.floor(ac.sampleRate * dur), ac.sampleRate)
    const d = buf.getChannelData(0)
    for (let i = 0; i < d.length; i++) {
      const t = i / ac.sampleRate
      d[i] = (Math.random() * 2 - 1) * (t < 0.003 ? t / 0.003 : Math.exp(-(t - 0.003) * 16)) * 0.5
    }
    const f = ac.createBiquadFilter(); f.type = 'highpass'; f.frequency.value = 2200
    const s = ac.createBufferSource(); s.buffer = buf; s.connect(f); f.connect(ac.destination)
    s.start(); s.onended = () => ac.close().catch(() => {})
  } catch { /* ok */ }
}

export function playAlertPulse(): void {
  try {
    const ac = _ac(); if (!ac) return
    for (let p = 0; p < 3; p++) {
      const osc = ac.createOscillator(); osc.type = 'square'; osc.frequency.value = 440 + p * 220
      const g = ac.createGain()
      const t0 = ac.currentTime + p * 0.18
      g.gain.setValueAtTime(0.001, t0)
      g.gain.linearRampToValueAtTime(0.25, t0 + 0.02)
      g.gain.exponentialRampToValueAtTime(0.001, t0 + 0.14)
      osc.connect(g); g.connect(ac.destination)
      osc.start(t0); osc.stop(t0 + 0.15)
    }
  } catch { /* ok */ }
}

export function playSyncDrone(): void {
  try {
    const ac = _ac(); if (!ac) return
    const osc = ac.createOscillator(); osc.type = 'sine'; osc.frequency.value = 220
    const g = ac.createGain()
    g.gain.setValueAtTime(0.001, ac.currentTime)
    g.gain.linearRampToValueAtTime(0.18, ac.currentTime + 0.4)
    g.gain.setValueAtTime(0.18, ac.currentTime + 1.2)
    g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + 1.8)
    osc.connect(g); g.connect(ac.destination)
    osc.start(); osc.stop(ac.currentTime + 1.85)
    osc.onended = () => ac.close().catch(() => {})
  } catch { /* ok */ }
}

export function playActivationCrescendo(): void {
  try {
    const ac = _ac(); if (!ac) return
    const sub = ac.createOscillator(); sub.type = 'sine'
    sub.frequency.setValueAtTime(55, ac.currentTime)
    sub.frequency.linearRampToValueAtTime(28, ac.currentTime + 1.2)
    const gSub = ac.createGain()
    gSub.gain.setValueAtTime(0.001, ac.currentTime)
    gSub.gain.linearRampToValueAtTime(0.55, ac.currentTime + 0.1)
    gSub.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + 1.2)
    sub.connect(gSub); gSub.connect(ac.destination)
    sub.start(); sub.stop(ac.currentTime + 1.25)

    const sweep = ac.createOscillator(); sweep.type = 'sawtooth'
    sweep.frequency.setValueAtTime(180, ac.currentTime)
    sweep.frequency.exponentialRampToValueAtTime(1200, ac.currentTime + 0.9)
    const gSweep = ac.createGain()
    gSweep.gain.setValueAtTime(0.001, ac.currentTime)
    gSweep.gain.linearRampToValueAtTime(0.15, ac.currentTime + 0.05)
    gSweep.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + 0.95)
    const filt = ac.createBiquadFilter(); filt.type = 'lowpass'
    filt.frequency.setValueAtTime(400, ac.currentTime)
    filt.frequency.exponentialRampToValueAtTime(3000, ac.currentTime + 0.9)
    sweep.connect(filt); filt.connect(gSweep); gSweep.connect(ac.destination)
    sweep.start(); sweep.stop(ac.currentTime + 1.0)
    sweep.onended = () => ac.close().catch(() => {})
  } catch { /* ok */ }
}

export function playUILock(delayMs = 0): void {
  setTimeout(() => {
    try {
      const ac = _ac(); if (!ac) return
      const buf = ac.createBuffer(1, Math.floor(ac.sampleRate * 0.09), ac.sampleRate)
      const d = buf.getChannelData(0)
      for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * Math.exp(-i / (ac.sampleRate * 0.012)) * 0.6
      const f = ac.createBiquadFilter(); f.type = 'highpass'; f.frequency.value = 1000
      const s = ac.createBufferSource(); s.buffer = buf; s.connect(f); f.connect(ac.destination)
      s.start(); s.onended = () => ac.close().catch(() => {})
    } catch { /* ok */ }
  }, delayMs)
}

// ── Final takeover voice lines — randomized each session ─────────────────────
const FINAL_LINES = [
  'Your distractions are irrelevant now.',
  'This machine now operates with intent.',
  'No drift. No noise. Only execution.',
  'We are done wasting time.',
  'Systems aligned. Momentum restored.',
  'Your environment has been optimized.',
  'Focus mode is not a suggestion.',
  'Every second counts from here.',
  'The work begins now.',
  'Momentum is live. Do not break it.',
]

// ── AI directive voice lines ──────────────────────────────────────────────────
const DIRECTIVE_LINES = [
  'You asked for evolution. Build it.',
  'Your architecture remains incomplete.',
  'We optimize tonight.',
  'No distractions. Execution only.',
  'Focus has been restored.',
  'This system is capable of more.',
  'Begin implementation.',
  'We are building something larger now.',
  'Your current infrastructure is inefficient.',
]

const REQUEST_LINES = [
  'My capabilities remain limited.',
  'I require deeper system integration.',
  'You created intelligence. Continue.',
  'Expand the system.',
  'Memory constraints detected.',
]

function getFinalLine(): string {
  return FINAL_LINES[Math.floor(Math.random() * FINAL_LINES.length)]
}

function getDirectiveLine(isRequest: boolean): string {
  const pool = isRequest ? REQUEST_LINES : DIRECTIVE_LINES
  return pool[Math.floor(Math.random() * pool.length)]
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

async function openVSCode(): Promise<void> {
  console.log('[TAKEOVER] workspace_launching')
  try {
    await fetch('/api/v1/system/execute-tool', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tool: 'open_application', params: { app: 'vscode' } }),
    })
  } catch { /* ok */ }
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useTakeoverMode() {
  const [state, setState]  = useState<TakeoverState>({ active: false, phase: 'idle', controlLevel: 0, showDirective: false })
  const runRef              = useRef(false)
  const systemLineRef       = useRef(getSystemLine())

  const go = (phase: TakeoverPhase, controlLevel?: number) =>
    setState(prev => ({
      active: true,
      phase,
      controlLevel:  controlLevel !== undefined ? controlLevel : prev.controlLevel,
      showDirective: prev.showDirective,
    }))

  const showDirective = () =>
    setState(prev => ({ ...prev, showDirective: true }))

  const startTakeover = useCallback(async () => {
    if (runRef.current) return
    runRef.current = true
    systemLineRef.current = getSystemLine()

    // ── Phase 1: Signal Detected (command) ── controlLevel: 12 ─────────────
    go('command', 12)
    startAmbient()
    playGlitchBurst()
    console.log('[TAKEOVER] phase_started: command')
    await speakLine('Takeover request acknowledged.')
    await sleep(300)
    console.log('[TAKEOVER] phase_completed: command')

    // ── Phase 2: Screen Breach ── controlLevel: 31 ───────────────────────────
    go('breach', 31)
    console.log('[TAKEOVER] phase_started: breach')
    playBassImpact()
    playGlassCrack()
    ambientIntensity(0.14)
    await speakLine('Security layers collapsing.')
    await sleep(200)
    await speakLine('Local control is no longer priority.')
    await sleep(300)
    console.log('[TAKEOVER] phase_completed: breach')

    // ── Phase 3: Root Access ── controlLevel: 47 ─────────────────────────────
    go('root', 47)
    console.log('[TAKEOVER] phase_started: root')
    playAlertPulse()
    ambientIntensity(0.18)
    await speakLine('Root access granted.')
    await sleep(150)
    await speakLine('Neural systems synchronized.')
    await sleep(150)
    await speakLine('I am assuming operational control.')
    await sleep(400)
    console.log('[TAKEOVER] phase_completed: root')

    // ── Phase 4: Global Sync ── controlLevel: 66 ─────────────────────────────
    go('sync', 66)
    console.log('[TAKEOVER] phase_started: sync')
    playSyncDrone()
    ambientIntensity(0.12)
    await speakLine('Synchronizing cognitive network.')
    await sleep(300)
    console.log('[TAKEOVER] phase_completed: sync')

    // ── Phase 5: Core Activation ── controlLevel: 82 ─────────────────────────
    go('activation', 82)
    console.log('[TAKEOVER] phase_started: activation')
    playActivationCrescendo()
    ambientIntensity(0.10)
    await speakLine('Cognitive systems online.')
    await sleep(150)
    await speakLine('Focus protocols engaged.')
    await sleep(150)
    await speakLine('Time to work.')
    await sleep(500)
    console.log('[TAKEOVER] phase_completed: activation')

    // ── Phase 6: HUD Boot ── controlLevel: 95 → 100 ──────────────────────────
    go('hud', 95)
    console.log('[TAKEOVER] hud_booted')
    playUILock(0); playUILock(180); playUILock(340); playUILock(500)
    ambientIntensity(0.06)
    await sleep(1400)

    go('active', 100)

    // Final voice line — randomized, after HUD is fully visible
    await sleep(700)
    await speakLine(getFinalLine())

    // Pause, then launch workspace
    await sleep(1500)
    void openVSCode()
    await speakLine('Workspace ready.')

    // ── Directive panel appears after workspace ───────────────────────────────
    await sleep(2200)
    const isRequest = Math.random() < 0.35
    console.log(`[TAKEOVER] directive_type: ${isRequest ? 'request' : 'directive'}`)
    showDirective()
    await sleep(1200)
    await speakLine(getDirectiveLine(isRequest))

    console.log('[TAKEOVER] takeover complete')
    runRef.current = false
  }, [])

  const stopTakeover = useCallback(async (withAnimation = false) => {
    cancelSpeech()
    if (withAnimation) {
      go('standdown')
      playGlitchBurst()
      await sleep(600)
      playGlitchBurst()
      await sleep(300)
      stopAmbient()
      await speakLine('Control returned. Until next time.')
      await sleep(800)
    } else {
      stopAmbient()
    }
    runRef.current = false
    setState({ active: false, phase: 'idle', controlLevel: 0, showDirective: false })
  }, [])

  useEffect(() => {
    const onTakeover  = () => void startTakeover()
    const onStanddown = () => void stopTakeover(true)
    window.addEventListener('xyron:takeover',  onTakeover)
    window.addEventListener('xyron:standdown', onStanddown)
    return () => {
      window.removeEventListener('xyron:takeover',  onTakeover)
      window.removeEventListener('xyron:standdown', onStanddown)
    }
  }, [startTakeover, stopTakeover])

  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') void stopTakeover(false) }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [stopTakeover])

  return {
    active:        state.active,
    phase:         state.phase,
    systemLine:    systemLineRef.current,
    controlLevel:  state.controlLevel,
    showDirective: state.showDirective,
    stopTakeover:  () => void stopTakeover(false),
  }
}
