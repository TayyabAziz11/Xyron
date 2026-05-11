'use client'

function genUUID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16)
  })
}

import { useState, useEffect, useRef, useCallback } from 'react'
import { useMicVAD } from '@ricky0123/vad-react'
import { readAssistantSettings, buildGreeting } from '@/hooks/useAssistantSettings'
import {
  type SessionState,
  type ConvMessage,
  type HistoryEntry,
  type SystemAction,
  isStopPhrase,
  checkIdentityResponse,
  detectSystemAction,
  resolveLocation,
  stripMarkdown,
  getApiBase,
  AudioQueue,
  streamAndSpeak,
  setPendingAudio,
  clearPendingAudio,
  isAudioActivated,
} from '@/lib/voice-core'

// ── Audio utility: Float32Array (16kHz mono) → WAV Blob ────────────────────────
// Silero VAD gives us raw PCM; Whisper accepts WAV. Standard 16-bit PCM encoding.
function float32ToWav(samples: Float32Array, sampleRate: number): Blob {
  const buf  = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buf)
  const writeStr = (off: number, s: string) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)) }
  writeStr(0, 'RIFF');  view.setUint32(4,  36 + samples.length * 2, true)
  writeStr(8, 'WAVE');  writeStr(12, 'fmt ')
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true);  view.setUint16(34, 16, true)
  writeStr(36, 'data'); view.setUint32(40, samples.length * 2, true)
  let off = 44
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(off, s < 0 ? s * 32768 : s * 32767, true); off += 2
  }
  return new Blob([buf], { type: 'audio/wav' })
}

export type { SessionState, ConvMessage }

// ── Constants ─────────────────────────────────────────────────────────────────

const STOP_RESPONSE = "Okay, stopping now. Talk to you later."

// Session auto-expires after this many ms of silence (no new commands)
const SESSION_TIMEOUT_MS = 30_000  // 30s idle; cleared on speech start, reset after each response

// ── State machine ─────────────────────────────────────────────────────────────
//
// Canonical flow:
//   idle → listening → transcribing → processing → speaking → idle → (wait)
//
// HARD RULES:
//   1. 'listening' can ONLY be entered from 'idle' via startListening().
//   2. 'speaking' NEVER jumps directly to 'listening'.
//   3. After speaking finishes → idle → STOP. User must tap mic again.
//   4. 'greeting' ends at 'idle', not 'listening' — no auto-start.

const ALLOWED: Partial<Record<SessionState, SessionState[]>> = {
  idle:         ['greeting', 'listening', 'stopped'],
  greeting:     ['idle', 'stopped'],              // greeting → idle, then WAIT
  listening:    ['transcribing', 'stopped'],
  transcribing: ['processing', 'idle', 'stopped'],
  processing:   ['speaking', 'idle', 'stopped'],
  speaking:     ['idle', 'stopped'],              // speaking → idle → WAIT
  stopped:      ['idle'],
}

function _failsafe(intent: string, result: { success?: boolean; spoken?: string } | null, fallback: string): string {
  if (!result || !result.success) {
    console.warn('[XYRON] Tool execution failed:', intent, result)
    return result?.spoken || fallback
  }
  return result.spoken || fallback
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useVoiceSession() {
  const [sessionState,   setSessionState]   = useState<SessionState>('idle')
  const [sessionActive,  setSessionActive]  = useState(false)
  const [messages,       setMessages]       = useState<ConvMessage[]>([])
  const [error,          setError]          = useState<string | null>(null)
  const [followUp,       setFollowUp]       = useState<string | null>(null)
  const [offlineMode,    setOfflineMode]    = useState(false)   // true when ollama is source

  // ── Core control refs ──────────────────────────────────────────────────────

  /** True while session is running (startSession called, stopSession not yet called). */
  const activeRef = useRef(false)

  /**
   * Single-instance guard for startListening.
   * Prevents double-tap / concurrent listen cycles.
   * Released when: speaking audio drains (onEmpty), or on error, or on stop.
   */
  const isRunningRef = useRef(false)

  /** State mirror — always up-to-date in closures (avoids stale React state). */
  const stateRef = useRef<SessionState>('idle')

  /** True while the SSE stream is open. Prevents onEmpty firing before stream ends. */
  const isStreamingRef = useRef(false)

  // ── Silero VAD audio ref ───────────────────────────────────────────────────
  // Receives Float32Array from Silero VAD; stored here for the process callback.
  const _vadAudioCallbackRef = useRef<(audio: Float32Array) => void>(() => {})
  /** Counts consecutive turns with no usable transcript. Resets on real speech. */
  const silentTurnsRef = useRef(0)

  // ── Voice confirmation flow ────────────────────────────────────────────────
  // When a high-risk action (e.g. delete_file) needs user confirmation,
  // we store the pending action here and intercept the next "yes"/"no".
  const pendingConfirmRef = useRef<{
    tool:    string
    params:  Record<string, unknown>
    prompt:  string   // the question already spoken to the user
  } | null>(null)

  // ── Streaming refs ────────────────────────────────────────────────────────

  const taskCtrlRef       = useRef<AbortController | null>(null)
  const queueRef          = useRef<AudioQueue | null>(null)
  const sessionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Universal context memory (intent + entities + last action) ───────────
  const lastActionRef = useRef<{ intent: string; action: SystemAction; transcript: string } | null>(null)
  // ── Folder creation context ───────────────────────────────────────────────
  const lastCreatedFolderRef  = useRef<{ name: string; path: string } | null>(null)
  // Tracks all paths created in the last create action for "delete them" resolution
  const lastCreatedFoldersRef = useRef<string[]>([])
  // Maps every created folder name (lowercase) → full Windows path, for resolving
  // "inside tayyab" references across nested creations (e.g. games/tayyab/gta).
  const folderMapRef = useRef<Map<string, string>>(new Map())
  const pendingFolderRef = useRef<
    { stage: 'name'; knownPath?: string } | { stage: 'path'; name: string } | null
  >(null)

  // ── Silero VAD (neural model — replaces hand-written RMS VAD) ────────────
  //
  // positiveSpeechThreshold=0.5 / negativeSpeechThreshold=0.35 are the Silero
  // defaults, tuned on 40+ languages. preSpeechPadFrames=3 keeps the first
  // syllable (prevents "reate folder" instead of "Create folder").
  // workletOptions.url must point to the asset we copied to public/.

  const vad = useMicVAD({
    startOnLoad:   false,
    model:         'v5',
    baseAssetPath: '/',        // serves silero_vad_v5.onnx + vad.worklet.bundle.min.js from public/
    onnxWASMBasePath: '/',     // serves ort-wasm-simd-threaded.wasm from public/
    positiveSpeechThreshold: 0.70,  // raised from 0.5 — filters background room noise
    negativeSpeechThreshold: 0.35,
    redemptionMs: 1500,             // require 1500ms of silence before closing segment — gives user time to pause mid-sentence
    minSpeechMs:    400,  // need 400ms of speech — avoids noise bursts and breaths
    preSpeechPadMs: 150,  // capture 150ms before detection fires (avoids clipping "Cr-eate")
    onSpeechStart: () => {
      console.log('[SILERO VAD] speech start — state:', stateRef.current)
      // Reset session idle timer on any speech activity
      if (sessionTimeoutRef.current) {
        clearTimeout(sessionTimeoutRef.current)
        sessionTimeoutRef.current = null
      }
      // Interrupt: user spoke while assistant was playing TTS
      if (stateRef.current === 'speaking') {
        console.log('[VOICE] interrupt — aborting TTS, transitioning to listening')
        taskCtrlRef.current?.abort()
        taskCtrlRef.current = null
        queueRef.current?.abort()
        queueRef.current = null
        isStreamingRef.current = false
        isRunningRef.current = true   // allow _processAudio guard to pass
        stateRef.current = 'listening'
        setSessionState('listening')
        window.dispatchEvent(new Event('xyron:tts-end'))
      }
    },
    onSpeechEnd: (audio) => {
      _vadAudioCallbackRef.current(audio)
    },
    onVADMisfire: () => {
      console.log('[SILERO VAD] misfire — too short, ignoring')
    },
  })

  // ── Session + history ─────────────────────────────────────────────────────

  const sessionIdRef   = useRef<string>('')
  const historyRef     = useRef<HistoryEntry[]>([])
  const currentModeRef = useRef<string | null>(null)
  const mediaSessionRef = useRef<{
    platform: 'youtube' | 'netflix' | 'spotify' | null
    isOpen: boolean
    tabRef: Window | null
    lastQuery: string | null
  }>({ platform: null, isOpen: false, tabRef: null, lastQuery: null })

  // ── State transition (validated) ──────────────────────────────────────────

  const transition = useCallback((next: SessionState) => {
    const prev = stateRef.current
    if (prev === next) return
    const allowed = ALLOWED[prev]
    if (!allowed?.includes(next)) {
      console.warn(`[VOICE] BLOCKED transition: ${prev} → ${next}`)
      return
    }
    console.log(`[VOICE] state: ${prev} → ${next}`)
    stateRef.current = next
    setSessionState(next)
  }, [])

  /** Emergency reset — bypasses state machine. Only for genuine deadlock recovery. */
  const forceIdle = useCallback(() => {
    console.warn('[VOICE] force reset → idle')
    isRunningRef.current  = false
    isStreamingRef.current = false
    stateRef.current = 'idle'
    setSessionState('idle')
  }, [])

  // ── Message helpers ───────────────────────────────────────────────────────

  const addMsg = useCallback(
    (role: ConvMessage['role'], text: string, status: ConvMessage['status'] = 'done'): string => {
      const id = genUUID()
      setMessages((prev) => {
        const next = [...prev, { id, role, text, status, timestamp: new Date() }]
        historyRef.current = next
          .filter((m) => m.role === 'user' || m.role === 'assistant')
          .map((m) => ({ role: m.role as 'user' | 'assistant', text: m.text }))
        return next
      })
      return id
    },
    [],
  )

  const updMsg = useCallback((id: string, patch: Partial<ConvMessage>) => {
    setMessages((prev) => {
      const next = prev.map((m) => (m.id === id ? { ...m, ...patch } : m))
      historyRef.current = next
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role as 'user' | 'assistant', text: m.text }))
      return next
    })
  }, [])

  // ── Stop VAD (pause Silero — mic stream stays open for instant resume) ────

  const stopMedia = useCallback(() => {
    vad.pause()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Abort in-flight stream + audio ────────────────────────────────────────

  const abortTask = useCallback(() => {
    taskCtrlRef.current?.abort()
    taskCtrlRef.current = null
    queueRef.current?.abort()
    queueRef.current = null
    isStreamingRef.current = false
  }, [])

  // ── Session idle timeout ──────────────────────────────────────────────────
  // Called after each response — if no new command arrives within SESSION_TIMEOUT_MS,
  // the session ends automatically (Jarvis behavior: goes quiet after a few seconds).

  const resetSessionTimeout = useCallback(() => {
    if (sessionTimeoutRef.current) clearTimeout(sessionTimeoutRef.current)
    if (!activeRef.current) return
    sessionTimeoutRef.current = setTimeout(() => {
      if (!activeRef.current) return
      console.log('[SESSION_FLOW] session_timeout — ending after', SESSION_TIMEOUT_MS / 1000, 's of inactivity')
      activeRef.current      = false
      isRunningRef.current   = false
      isStreamingRef.current = false
      sessionTimeoutRef.current = null
      vad.pause()
      setSessionActive(false)
      stateRef.current = 'idle'
      setSessionState('idle')
      window.dispatchEvent(new Event('xyron:tts-end'))
      window.dispatchEvent(new Event('xyron:session-end'))
    }, SESSION_TIMEOUT_MS)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── TTS for fast-paths (greeting, identity, system) ───────────────────────

  const speakResponse = useCallback(async (raw: string, externalSignal?: AbortSignal): Promise<void> => {
    const text = stripMarkdown(raw)
    if (!text) return
    const API_BASE = getApiBase()
    const { voice, speed, voiceEnabled, volume } = readAssistantSettings()
    console.log('[SPEAKRESP] entry — len:', text.length, 'voiceEnabled:', voiceEnabled, 'vol:', volume, 'activated:', isAudioActivated())
    if (!voiceEnabled) return

    window.dispatchEvent(new Event('xyron:tts-start'))
    // Clear idle timer while TTS is playing — prevents timeout firing mid-speech
    if (sessionTimeoutRef.current) {
      clearTimeout(sessionTimeoutRef.current)
      sessionTimeoutRef.current = null
    }
    console.log('[FLOW] tts_playing')
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          if (externalSignal?.aborted) break
          console.log('[SPEAKRESP] fetch attempt', attempt + 1)
          const ctrl    = new AbortController()
          const timeout = setTimeout(() => ctrl.abort(), 30_000)
          externalSignal?.addEventListener('abort', () => ctrl.abort(), { once: true })
          let resp: Response
          try {
            resp = await fetch(`${API_BASE}/api/v1/voice/synthesize`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ text, voice, speed }), signal: ctrl.signal,
            })
          } finally { clearTimeout(timeout) }
          console.log('[SPEAKRESP] fetch status:', resp!.status, 'ok:', resp!.ok)
          if (resp!.ok) {
            const blob = await resp!.blob()
            console.log('[SPEAKRESP] blob size:', blob.size, 'type:', blob.type)
            const url = URL.createObjectURL(blob)
            await new Promise<void>((resolve) => {
              const audio  = new Audio(url)
              audio.volume = volume
              let resolved = false
              const safeResolve = () => { if (!resolved) { resolved = true; resolve() } }
              const cleanup     = () => URL.revokeObjectURL(url)
              const t = setTimeout(() => { console.warn('[SPEAKRESP] safety timeout fired'); cleanup(); safeResolve() }, (Math.max(6, text.length / 8) / speed) * 1000 + 10_000)
              audio.onended = () => { console.log('[SPEAKRESP] audio ended naturally'); clearTimeout(t); cleanup(); safeResolve() }
              audio.onerror = (e) => { console.error('[SPEAKRESP] audio onerror:', e); clearTimeout(t); cleanup(); safeResolve() }
              // Pre-prime: resolve at 85% playback — mic restarts before TTS fully ends
              audio.ontimeupdate = () => {
                if (!resolved && audio.duration > 0 && audio.currentTime / audio.duration >= 0.85) {
                  audio.ontimeupdate = null
                  console.log('[FLOW] tts_near_end — pre-priming mic', audio.currentTime.toFixed(2), '/', audio.duration.toFixed(2))
                  safeResolve()   // audio keeps playing; onended cleans up url
                }
              }
              console.log('[SPEAKRESP] calling audio.play(), readyState:', audio.readyState)
              audio.play()
                .then(() => console.log('[SPEAKRESP] play() resolved — audio is playing'))
                .catch((err: unknown) => {
                  const errName = err instanceof Error ? err.name : String(err)
                  console.error('[SPEAKRESP] play() error:', errName, err instanceof Error ? err.message : '')
                  if (err instanceof DOMException && err.name === 'NotAllowedError') {
                    // Single-slot: replaces any stale greeting from a previous false wake.
                    // Resolve immediately — do NOT hang; pending audio replays on next click.
                    setPendingAudio(() => { audio.play().catch(() => { clearTimeout(t); cleanup(); safeResolve() }) })
                    clearTimeout(t)
                    safeResolve()
                  } else {
                    clearTimeout(t); cleanup(); safeResolve()
                  }
                })
            })
            return
          }
        } catch (e) { console.warn('[SPEAKRESP] attempt error:', e) }
      }
      // TTS failed — text is shown on screen; skip browser speech synthesis to avoid robotic fallback voice
      console.warn('[SPEAKRESP] all TTS attempts failed — silent')
    } finally {
      window.dispatchEvent(new Event('xyron:tts-end'))
    }
  }, [])

  // ── Main listen function — ref-based so it's always fresh ────────────────
  //
  // Triggers: (1) maybeRestartListening after each response, (2) wake word, (3) user tap.
  // Strict guards prevent any race condition — see _startListening guards below.

  const _startListeningRef = useRef<() => Promise<void>>(async () => {})

  // ── Safe auto-restart (continuous conversation) ───────────────────────────
  //
  // THIS IS THE ONLY PLACE WHERE LISTENING RESTARTS AUTOMATICALLY.
  // Called after every response (GPT audio drain, identity, system action).
  // Five guards make it impossible to overlap states or race conditions:
  //   1. state must be 'idle'        — no mid-state restarts
  //   2. not streaming               — SSE still open? wait
  //   3. audio queue not playing     — audio still playing? wait
  //   4. audio queue has no pending  — chunks queued but not yet played? wait
  //   5. session must be active      — session ended? stop

  const maybeRestartListening = useCallback(() => {
    const reason =
      stateRef.current !== 'idle'                   ? `state=${stateRef.current}` :
      isStreamingRef.current                        ? 'streaming active' :
      (queueRef.current?.isPlaying ?? false)        ? 'audio playing' :
      (queueRef.current?.hasPendingAudio ?? false)  ? 'audio pending' :
      !activeRef.current                            ? 'session inactive' :
      null

    if (reason) {
      console.log(`[SESSION_FLOW] listen_blocked — ${reason}`)
      return
    }

    console.log('[SESSION_FLOW] listening_restarted — VAD starting now, active:', activeRef.current)
    console.log('[FLOW] mic_restarted')
    resetSessionTimeout()  // arm 30s idle timer; cleared on speech start
    void _startListeningRef.current()
  }, [resetSessionTimeout])

  _startListeningRef.current = async function _startListening(): Promise<void> {
    // ── Guard 1: session must be active ──────────────────────────────────────
    if (!activeRef.current) {
      console.log('[VOICE] startListening: blocked — session inactive')
      return
    }
    // ── Guard 2: must be in idle state ────────────────────────────────────────
    if (stateRef.current !== 'idle') {
      console.log(`[VOICE] startListening: blocked — state=${stateRef.current} (must be idle)`)
      return
    }
    // ── Guard 3: no concurrent listen cycle ───────────────────────────────────
    if (isRunningRef.current) {
      console.log('[VOICE] startListening: blocked — already running')
      return
    }
    // ── Guard 4: no active audio / stream ─────────────────────────────────────
    if (isStreamingRef.current || queueRef.current?.isPlaying || queueRef.current?.hasPendingAudio) {
      console.log('[VOICE] startListening: blocked — audio still active')
      return
    }

    // Guard 5: VAD must be ready — loading=true means ONNX model not yet initialized,
    // errored=true means MicVAD.new() threw (usually: SharedArrayBuffer blocked by
    // missing COOP/COEP headers, or mic permission denied).
    if (vad.loading) {
      console.warn('[SESSION_FLOW] listen_blocked — VAD still loading ONNX model, retry in 1500ms')
      setTimeout(() => maybeRestartListening(), 1500)
      return
    }
    if (vad.errored) {
      console.error('[SESSION_FLOW] VAD errored (SharedArrayBuffer/COOP-COEP issue?):', vad.errored)
      setError('Voice microphone failed to initialize — please refresh the page.')
      return
    }

    isRunningRef.current = true
    abortTask()

    console.log('[SESSION_FLOW] mic_stream_active — VAD starting (listening was:', vad.listening, ')')
    console.log('[FLOW] vad_started')
    transition('listening')
    try {
      await vad.start()
      console.log('[FLOW] mic_active')
    } catch (err) {
      console.error('[SESSION_FLOW] vad.start() threw:', err)
      isRunningRef.current = false
      transition('idle')
      setTimeout(() => maybeRestartListening(), 2000)
    }
  }

  // ── Process utterance from Silero VAD ─────────────────────────────────────
  // Assigned on every render so closures see fresh state (same pattern as
  // _startListeningRef). Called by useMicVAD.onSpeechEnd.

  _vadAudioCallbackRef.current = async function _processAudio(vadAudio: Float32Array): Promise<void> {
    if (!activeRef.current || stateRef.current !== 'listening' || !isRunningRef.current) return

    const API_BASE = getApiBase()
    vad.pause()   // stop listening while we process; resumed after speaking

    // ══════════════════════════════════════════════════════════════════════
    // PHASE 2 — TRANSCRIBE  (Float32Array 16kHz → WAV → Whisper)
    // ══════════════════════════════════════════════════════════════════════

    console.log('[SESSION_FLOW] transcription_started')
    console.log('[FLOW] transcription_sent')
    transition('transcribing')

    const t0 = performance.now()
    const blob = float32ToWav(vadAudio, 16000)

    // Silero's minSpeechFrames already guards against noise; the WAV size
    // check is a last-resort safety net for truly empty utterances.
    if (blob.size < 2_000) {
      transition('idle')
      isRunningRef.current = false
      if (++silentTurnsRef.current < 5) {
        maybeRestartListening()
      } else {
        silentTurnsRef.current = 0
        activeRef.current = false   // full stop — button resets so next press starts fresh
        stopMedia()
        setSessionActive(false)
      }
      return
    }

    const t1 = performance.now()
    console.log(`[PERF] t1-t0 (speech end → transcribe request): ${(t1 - t0).toFixed(0)}ms`)

    let transcript = ''
    try {
      const form = new FormData()
      form.append('audio', blob, 'recording.wav')
      // 8-second hard timeout — returns to idle if Whisper API hangs
      const _tCtrl = new AbortController()
      const _tTimer = setTimeout(() => _tCtrl.abort(), 8_000)
      try {
        const r = await fetch(`${API_BASE}/api/v1/voice/transcribe`, {
          method: 'POST', body: form, signal: _tCtrl.signal,
        })
        transcript = ((await r.json())?.data?.text ?? '').trim()
      } finally {
        clearTimeout(_tTimer)
      }
    } catch { /* timeout or network — fall through to idle */ }

    const t1b = performance.now()
    console.log(`[PERF] transcribe RTT: ${(t1b - t1).toFixed(0)}ms — result: ${transcript ? JSON.stringify(transcript.slice(0, 60)) : '(empty)'}`)

    if (!transcript) {
      console.log('[SESSION_FLOW] transcription_ended — empty, restarting listen')
      transition('idle')
      isRunningRef.current = false
      if (++silentTurnsRef.current < 5) {
        maybeRestartListening()
      } else {
        silentTurnsRef.current = 0
        activeRef.current = false
        stopMedia()
        setSessionActive(false)
        window.dispatchEvent(new Event('xyron:session-end'))
      }
      return
    }

    // Real speech received — reset the silence counter
    silentTurnsRef.current = 0
    console.log('[SESSION_FLOW] transcription_ended —', JSON.stringify(transcript))
    console.log('[FLOW] response_received —', JSON.stringify(transcript.slice(0, 60)))

    // ══════════════════════════════════════════════════════════════════════
    // PHASE 3 — ROUTE
    // ══════════════════════════════════════════════════════════════════════

    // ── Voice confirmation intercept ──────────────────────────────────────
    // If a high-risk action is waiting for yes/no, intercept before all other routing.
    if (pendingConfirmRef.current) {
      const pending = pendingConfirmRef.current
      pendingConfirmRef.current = null   // clear immediately — one-shot
      const lower = transcript.toLowerCase().trim().replace(/[.!?,;:]+$/, '')
      const isYes = /^(yes|yeah|yep|confirm|do it|go ahead|sure|ok|okay|correct|affirmative)$/.test(lower)
      const isNo  = /^(no|nope|cancel|stop|abort|never mind|nevermind|don't)$/.test(lower)
      addMsg('user', transcript)
      if (isYes) {
        // Execute the confirmed action via backend tool endpoint
        const aId = addMsg('assistant', 'Okay, doing it now…')
        transition('processing')
        try {
          let d: Record<string, unknown> | null = null
          if (pending.tool === '__delete_multiple__') {
            const resp = await fetch(`${API_BASE}/api/v1/system/delete-multiple`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(pending.params),
            })
            d = await resp.json().catch(() => null)
          } else {
            const resp = await fetch(`${API_BASE}/api/v1/system/execute-tool`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ tool: pending.tool, params: { ...pending.params, confirmed: true } }),
            })
            d = await resp.json().catch(() => null)
          }
          const spoken = (d?.spoken as string) || (d?.message as string) || 'Done.'
          updMsg(aId, { text: spoken })
          if (d?.success) lastCreatedFoldersRef.current = []
          transition('speaking')
          await speakResponse(spoken)
        } catch {
          const err = 'Sorry, I had trouble completing that action.'
          updMsg(aId, { text: err })
          transition('speaking')
          await speakResponse(err)
        }
      } else if (isNo) {
        const cancelled = 'Cancelled — no changes made.'
        addMsg('assistant', cancelled)
        transition('speaking')
        await speakResponse(cancelled)
      } else {
        // Ambiguous — re-ask
        const reAsk = `I didn't catch that. ${pending.prompt}`
        pendingConfirmRef.current = pending   // restore
        addMsg('assistant', reAsk)
        transition('speaking')
        await speakResponse(reAsk)
      }
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // ── Pending folder multi-turn ─────────────────────────────────────────
    if (pendingFolderRef.current) {
      const pf = pendingFolderRef.current
      addMsg('user', transcript)
      const rawInput = transcript.replace(/[.!?,;:]+$/, '').trim()

      // Cancel words: user wants to abandon the pending folder dialog
      if (/^(?:cancel|stop|abort|quit|forget\s+(?:it|that)|never\s+mind|nevermind|leave\s+(?:it|me)|skip\s+it|no(?:\s+thanks)?|nope|don'?t|exit)$/i.test(rawInput)) {
        pendingFolderRef.current = null
        const cancelled = "No problem, cancelled."
        addMsg('assistant', cancelled)
        transition('speaking')
        await speakResponse(cancelled)
        transition('idle')
        isRunningRef.current = false
        maybeRestartListening()
        return
      }

      if (pf.stage === 'name') {
        const name = rawInput
          .replace(/^(?:name\s+it|call\s+it|it(?:'?s|\s+is)?|the\s+name\s+is?|name\s+is?)\s+/i, '')
          .trim()
        if (!pf.knownPath) {
          // Have name but still no location — ask for it
          pendingFolderRef.current = { stage: 'path', name }
          const q = `Got it — "${name}". Where should I create it? Desktop, D drive, or somewhere else?`
          addMsg('assistant', q)
          transition('speaking')
          await speakResponse(q)
          transition('idle')
          isRunningRef.current = false
          maybeRestartListening()
          return
        }
        const targetPath = pf.knownPath
        pendingFolderRef.current = null
        const holdMsg = `Creating the "${name}" folder now.`
        const aId = addMsg('assistant', holdMsg)
        transition('speaking')
        const [, result] = await Promise.all([
          speakResponse(holdMsg),
          fetch(`${API_BASE}/api/v1/system/create-folder`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, path: targetPath }),
          }).then(r => r.json()).catch(() => null),
        ])
        transition('processing')
        let spoken: string
        if (result?.success) {
          const cp = result.path || targetPath
          lastCreatedFolderRef.current = { name, path: cp }
          lastCreatedFoldersRef.current = [cp]
          folderMapRef.current.set(name.toLowerCase(), cp)
          spoken = _failsafe('create_folder', result, `Done! I created the "${name}" folder. Say "open it" to open it.`)
        } else {
          spoken = _failsafe('create_folder', result, `Couldn't create the folder. Please try again.`)
        }
        updMsg(aId, { text: spoken })
        transition('speaking')
        await speakResponse(spoken)
        transition('idle')
        isRunningRef.current = false
        maybeRestartListening()
        return
      }

      if (pf.stage === 'path') {
        const { name } = pf
        // Clean up stutter/repeated phrase: "In D drive. In D drive." → "D drive"
        // Take only the first location clause (before any punctuation or repeated prep)
        const pathStr = rawInput
          .replace(/[.!?].*$/i, '')           // cut at first sentence-ending punctuation
          .replace(/^(?:in|on|inside|at)\s+(?:the\s+)?/i, '')  // strip leading prep
          .trim()
        pendingFolderRef.current = null
        const holdMsg = `Creating the "${name}" folder now.`
        const aId = addMsg('assistant', holdMsg)
        transition('speaking')
        const [, result] = await Promise.all([
          speakResponse(holdMsg),
          fetch(`${API_BASE}/api/v1/system/create-folder`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, path: pathStr }),
          }).then(r => r.json()).catch(() => null),
        ])
        transition('processing')
        let spoken: string
        if (result?.success) {
          const cp2 = result.path || pathStr
          lastCreatedFolderRef.current = { name, path: cp2 }
          lastCreatedFoldersRef.current = [cp2]
          folderMapRef.current.set(name.toLowerCase(), cp2)
          spoken = _failsafe('create_folder', result, `Done! I created the "${name}" folder in ${pathStr}. Say "open it" to open it.`)
        } else {
          spoken = _failsafe('create_folder', result, `Couldn't create the folder. Please try again.`)
        }
        updMsg(aId, { text: spoken })
        transition('speaking')
        await speakResponse(spoken)
        transition('idle')
        isRunningRef.current = false
        maybeRestartListening()
        return
      }
    }

    // ── Stop command ──────────────────────────────────────────────────────
    if (isStopPhrase(transcript)) {
      console.log('[VOICE] route: stop command')
      activeRef.current = false
      abortTask()
      stopMedia()
      addMsg('user', transcript)
      addMsg('assistant', STOP_RESPONSE)
      transition('processing')
      transition('speaking')
      await speakResponse(STOP_RESPONSE)
      transition('stopped')
      isRunningRef.current = false
      setSessionActive(false)
      setTimeout(forceIdle, 1500)
      return
    }

    // ── Identity fast-path ────────────────────────────────────────────────
    const { mode: _mode } = readAssistantSettings()
    const identityReply = checkIdentityResponse(transcript, _mode)
    if (identityReply) {
      console.log('[VOICE] route: identity')
      addMsg('user', transcript)
      addMsg('assistant', identityReply)
      transition('processing')
      transition('speaking')
      await speakResponse(identityReply)
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()   // ← safe auto-restart after speaking
      return
    }

    // ── System action fast-path ───────────────────────────────────────────
    const sysAction = detectSystemAction(transcript)
    if (sysAction) {
      console.log('[VOICE] route: system-action', sysAction)
      transition('processing')

      // "Do it again" — re-run the last stored action
      if (sysAction.intent === 'repeat_last') {
        if (lastActionRef.current) {
          const { action, transcript: prevT } = lastActionRef.current
          await _handleSystemAction(action, prevT, API_BASE)
        } else {
          addMsg('user', transcript)
          const msg = "I don't have a previous action to repeat."
          addMsg('assistant', msg)
          transition('speaking')
          await speakResponse(msg)
          transition('idle')
          isRunningRef.current = false
          maybeRestartListening()
        }
        return
      }

      // Store this action as last for future "repeat" / "do it again"
      if (sysAction.intent) lastActionRef.current = { intent: sysAction.intent, action: sysAction, transcript }

      await _handleSystemAction(sysAction, transcript, API_BASE)
      // _handleSystemAction ends with: transition('idle') + isRunningRef.current = false
      return
    }

    const t2 = performance.now()
    console.log(`[PERF] t2-t0 (speech end → GPT request): ${(t2 - t0).toFixed(0)}ms`)
    console.log('[VOICE] route: GPT stream')

    // ══════════════════════════════════════════════════════════════════════
    // PHASE 4 — STREAMING AI RESPONSE
    // ══════════════════════════════════════════════════════════════════════

    addMsg('user', transcript)
    transition('processing')
    const aId = addMsg('assistant', '', 'processing')

    const ctrl = new AbortController()
    taskCtrlRef.current = ctrl

    const { voice, speed, volume, mode: personalityMode } = readAssistantSettings()
    const history = historyRef.current.slice(-20)
    let chunkText = ''
    isStreamingRef.current = true

    const queue = new AudioQueue({
      volume,
      onEmpty: () => {
        // All audio drained — release the run lock and transition to idle.
        // maybeRestartListening() immediately re-checks all guards and begins
        // the next listen cycle if the session is still active and truly quiet.
        isStreamingRef.current = false
        isRunningRef.current   = false
        const s = stateRef.current
        console.log(`[VOICE] queue empty → idle (was: ${s})`)
        if (!ctrl.signal.aborted && (s === 'speaking' || s === 'processing')) {
          transition('idle')
          maybeRestartListening()
        }
      },
    })
    queueRef.current = queue

    try {
      await streamAndSpeak(
        transcript, history, queue, ctrl.signal, voice, speed, API_BASE,
        {
          onChunk: (text, _idx) => {
            if (!chunkText) {
              const t3 = performance.now()
              console.log(`[PERF] t3-t0 (speech end → first TTS chunk): ${(t3 - t0).toFixed(0)}ms  ← goal <500ms`)
              console.log(`[PERF] t3-t2 (GPT request → first chunk): ${(t3 - t2).toFixed(0)}ms`)
              transition('speaking')
              console.log('[VOICE] speaking: first chunk')
            }
            chunkText += (chunkText ? ' ' : '') + text
            updMsg(aId, { text: chunkText, status: 'processing' })
          },

          onDone: (fullText, source) => {
            console.log('[VOICE] stream: done')
            isStreamingRef.current = false
            updMsg(aId, { text: fullText || chunkText, status: 'done' })
            if (source === 'ollama') setOfflineMode(true)
            else setOfflineMode(false)
            // Do NOT release isRunningRef here — wait for queue.onEmpty (audio must finish first)
          },

          onError: (msg) => {
            if (ctrl.signal.aborted) return
            console.warn('[VOICE] stream: error', msg)
            isStreamingRef.current = false
            isRunningRef.current   = false
            updMsg(aId, { text: msg || 'Sorry, something went wrong.', status: 'error' })
            queue.abort()
            forceIdle()
            setTimeout(() => maybeRestartListening(), 1500)
          },

          onToolAction: (tool, params, _spoken) => {
            if (tool === 'takeover_mode') {
              window.dispatchEvent(new Event('xyron:takeover'))
              return // hook handles VS Code launch after sequence completes
            }
            if (tool === 'stand_down') {
              window.dispatchEvent(new Event('xyron:standdown'))
              return
            }
            fetch(`${API_BASE}/api/v1/system/execute-tool`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ tool, params }),
            }).catch(() => {})
          },

          onAction: (actionUrl, actionApp, actionPath) => {
            if (actionUrl && typeof window !== 'undefined') window.open(actionUrl, '_blank', 'noopener')
            if (actionApp) {
              fetch(`${API_BASE}/api/v1/system/open-app`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ app: actionApp }),
              }).catch(() => {})
            }
            if (actionPath) {
              fetch(`${API_BASE}/api/v1/system/open-directory`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: actionPath }),
              }).catch(() => {})
            }
          },

          onConfirmRequired: ({ tool, params, prompt }) => {
            // Store the pending action — the NEXT voice turn will intercept it
            pendingConfirmRef.current = { tool, params, prompt }
            // The spoken confirmation prompt is already in the GPT stream text
          },

          onFollowUp: (suggestion) => {
            setFollowUp(suggestion)
            // Auto-dismiss after 8s
            setTimeout(() => setFollowUp(null), 8000)
          },

          onProfileSwitch: (profile, voice) => {
            // Save to the canonical settings key so readAssistantSettings() picks it up.
            // The old code was writing to 'ai-operator:assistant-settings' which is never read.
            try {
              const raw  = localStorage.getItem('xyron:assistant-settings')
              const prev = raw ? JSON.parse(raw) : {}
              const next = { ...prev, mode: profile as any, voice: voice as any }
              localStorage.setItem('xyron:assistant-settings', JSON.stringify(next))
            } catch { /* ok */ }
            if (profile === 'chill') {
              setFollowUp('Chill mode on — ask me what to watch, Netflix picks or YouTube vibes?')
              setTimeout(() => setFollowUp(null), 12000)
            }
          },

          onModeChange: (mode) => {
            currentModeRef.current = mode
            const modeMessages: Record<string, string> = {
              morning:       'Morning routine started — weather, calendar, and music opened.',
              jarvis:        'Welcome home — system stats ready.',
              entertainment: 'Entertainment mode — opening content for you.',
            }
            const hint = modeMessages[mode]
            if (hint) {
              setFollowUp(hint)
              setTimeout(() => setFollowUp(null), 8000)
            }
          },

          onSystemConfirm: (action) => {
            const prompts: Record<string, string> = {
              shutdown: "Say 'yes' to confirm shutdown, or 'cancel' to abort.",
              restart:  "Say 'yes' to confirm restart, or 'cancel' to abort.",
            }
            const prompt = prompts[action] ?? `Confirm ${action}?`
            setFollowUp(prompt)
            setTimeout(() => setFollowUp(null), 15000)
          },
        },
        sessionIdRef.current,
        personalityMode,
      )
    } catch (e) {
      if (!ctrl.signal.aborted) {
        console.error('[VOICE] stream: exception', e)
        isStreamingRef.current = false
        isRunningRef.current   = false
        updMsg(aId, { text: 'Sorry, my response was interrupted. Please ask again.', status: 'error' })
        queue.abort()
        forceIdle()
        setTimeout(() => maybeRestartListening(), 1500)
      }
    }

    // SSE stream has ended. Audio queue may still be draining.
    // onEmpty handles the final → idle transition.
    // isRunningRef is released in onEmpty (not here).
  }

  // ── Inline system action handler ──────────────────────────────────────────
  //
  // Defined as a closure inside the hook body so it has access to all callbacks.
  // Ends with: transition('idle') + isRunningRef.current = false — always.
  // NO auto-restart.

  async function _handleSystemAction(
    sysAction: SystemAction, transcript: string, API_BASE: string,
  ) {
    // ── "Remember that X" ─────────────────────────────────────────────────
    if (sysAction.rememberFact) {
      addMsg('user', transcript)
      const aId = addMsg('assistant', sysAction.response)
      // Store in backend (fire-and-forget OK — confirmaton is spoken regardless)
      fetch(`${API_BASE}/api/v1/voice/remember`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fact: sysAction.rememberFact }),
      }).catch(() => {})
      updMsg(aId, { text: sysAction.response })
      transition('speaking')
      await speakResponse(sysAction.response)
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // ── "What do you remember?" ───────────────────────────────────────────
    if (sysAction.queryMemory) {
      addMsg('user', transcript)
      const aId = addMsg('assistant', sysAction.response)
      transition('speaking')
      const holdPromise = speakResponse(sysAction.response)
      const memPromise  = fetch(`${API_BASE}/api/v1/voice/memories`)
        .then(r => r.json()).catch(() => null)
      const [, memData] = await Promise.all([holdPromise, memPromise])
      const spoken = (memData?.spoken as string | undefined) || "I don't have anything stored about you yet."
      transition('processing')
      updMsg(aId, { text: spoken })
      transition('speaking')
      await speakResponse(spoken)
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // ── "Time to work" wake flow ──────────────────────────────────────────
    if (sysAction.workMode) {
      addMsg('user', transcript)
      const aId = addMsg('assistant', sysAction.response)
      fetch(`${API_BASE}/api/v1/system/open-app`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ app: 'vscode' }),
      }).then(r => r.json()).then(res => console.log('[TOOL] work_mode vscode:', res)).catch(() => {})
      if (typeof window !== 'undefined')
        window.open('https://github.com/TayyabAziz11', '_blank', 'noopener')
      updMsg(aId, { text: sysAction.response })
      transition('speaking')
      await speakResponse(sysAction.response)
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // ── Chill mode ────────────────────────────────────────────────────────
    if (sysAction.chillMode) {
      addMsg('user', transcript)
      const aId = addMsg('assistant', sysAction.response)
      try {
        const raw  = localStorage.getItem('ai-operator:assistant-settings')
        const prev = raw ? JSON.parse(raw) : {}
        localStorage.setItem('xyron:assistant-settings', JSON.stringify({ ...prev, mode: 'chill', voice: 'shimmer' }))
      } catch { /* ok */ }
      currentModeRef.current = 'chill'
      if (typeof window !== 'undefined') {
        // Open YouTube (tracked — "play X" commands reuse this tab)
        if (!mediaSessionRef.current.isOpen || mediaSessionRef.current.platform !== 'youtube') {
          const tab = window.open('https://www.youtube.com', '_blank')
          mediaSessionRef.current = { platform: 'youtube', isOpen: true, tabRef: tab, lastQuery: null }
        }
        // Always open Netflix alongside YouTube
        window.open('https://www.netflix.com', '_blank', 'noopener')
      }
      setFollowUp('YouTube & Netflix open — say "play trending music", "play lo-fi", or ask for Netflix recommendations.')
      setTimeout(() => setFollowUp(null), 15000)
      updMsg(aId, { text: sysAction.response })
      transition('speaking')
      await speakResponse(sysAction.response)
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // ── Folder creation ───────────────────────────────────────────────────
    if (sysAction.createFolder != null) {
      const { name, path, subfolders, insideFolder } = sysAction.createFolder as {
        name?: string; path?: string; subfolders?: string[]; insideFolder?: string
      }

      // Resolve the target parent path from path + insideFolder:
      //   path only        → drive/special location (e.g. "d" → D:\)
      //   insideFolder only → look up in folder history map
      //   both             → history lookup first; fall back to drive\folder compound
      const resolvedPath: string | undefined = (() => {
        if (path && insideFolder) {
          const fromHistory = folderMapRef.current.get(insideFolder.toLowerCase())
          if (fromHistory) return fromHistory
          // Construct compound Windows path: "D:\\" + "games" → "D:\\games"
          const drive = resolveLocation(path)  // "d" → "D:\\"
          return drive.endsWith('\\') ? drive + insideFolder : drive + '\\' + insideFolder
        }
        if (insideFolder) return folderMapRef.current.get(insideFolder.toLowerCase())
        return path
      })()

      const _createWithSubs = async (folderName: string, folderPath: string): Promise<{ spoken: string; createdPath: string }> => {
        const result = await fetch(`${API_BASE}/api/v1/system/create-folder`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: folderName, path: folderPath }),
        }).then(r => r.json()).catch(() => null)

        if (!result?.success) {
          const spk = result?.spoken || `Couldn't create the folder. Please try again.`
          return { spoken: spk, createdPath: '' }
        }

        const createdPath = result.path || folderPath
        lastCreatedFolderRef.current = { name: folderName, path: createdPath }
        lastCreatedFoldersRef.current = [createdPath]
        folderMapRef.current.set(folderName.toLowerCase(), createdPath)

        if (subfolders && subfolders.length > 0) {
          const subResult = await fetch(`${API_BASE}/api/v1/system/create-subfolders`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ parent: createdPath, names: subfolders }),
          }).then(r => r.json()).catch(() => null)
          if (subResult?.success) {
            // Register each subfolder so "inside [subfolder]" works next
            for (const sub of subfolders) {
              folderMapRef.current.set(sub.toLowerCase(), `${createdPath}\\${sub}`)
            }
          }
          const subNote = subResult?.success
            ? ` with subfolders ${subfolders.map(s => `"${s}"`).join(', ')}`
            : ''
          return { spoken: `Done! Created "${folderName}"${subNote}. Say "open it" to open it.`, createdPath }
        }
        return { spoken: _failsafe('create_folder', result, `Done! I created the "${folderName}" folder. Say "open it" to open it.`), createdPath }
      }

      addMsg('user', transcript)
      if (name && resolvedPath) {
        const aId = addMsg('assistant', sysAction.response)
        transition('speaking')
        const [, { spoken }] = await Promise.all([
          speakResponse(sysAction.response),
          _createWithSubs(name, resolvedPath),
        ])
        transition('processing')
        updMsg(aId, { text: spoken })
        transition('speaking')
        await speakResponse(spoken)
      } else if (name && insideFolder) {
        // insideFolder was specified but not found in history — ask for location
        const q = `I don't know where "${insideFolder}" is. Which drive or folder should I create "${name}" in?`
        pendingFolderRef.current = { stage: 'path', name }
        addMsg('assistant', q)
        transition('speaking')
        await speakResponse(q)
      } else if (name) {
        // Name given but no location — ask user where
        pendingFolderRef.current = { stage: 'path', name }
        addMsg('assistant', sysAction.response)
        transition('speaking')
        await speakResponse(sysAction.response)
      } else {
        // Need name — ask
        pendingFolderRef.current = { stage: 'name', knownPath: resolvedPath || undefined }
        addMsg('assistant', sysAction.response)
        transition('speaking')
        await speakResponse(sysAction.response)
      }
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // ── Open last folder ──────────────────────────────────────────────────
    if (sysAction.openLastFolder) {
      addMsg('user', transcript)
      if (lastCreatedFolderRef.current) {
        const { name, path } = lastCreatedFolderRef.current
        fetch(`${API_BASE}/api/v1/system/open-directory`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path }),
        }).catch(() => {})
        const spoken = `Opening it now — the "${name}" folder.`
        addMsg('assistant', spoken)
        transition('speaking')
        await speakResponse(spoken)
      } else {
        const spoken = "I don't have a recently created folder on record. Try saying 'create folder' first."
        addMsg('assistant', spoken)
        transition('speaking')
        await speakResponse(spoken)
      }
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // ── Standalone subfolder creation ─────────────────────────────────────
    if (sysAction.createSubfolders) {
      const { count, names } = sysAction.createSubfolders
      addMsg('user', transcript)
      const aId = addMsg('assistant', sysAction.response)
      transition('speaking')
      speakResponse(sysAction.response)  // speak while creating
      const parent = lastCreatedFolderRef.current?.path ?? ''
      const result = await fetch(`${API_BASE}/api/v1/system/create-subfolders`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent, names: names.length > 0 ? names : undefined, count }),
      }).then(r => r.json()).catch(() => null)
      if (result?.success && parent && names.length > 0) {
        lastCreatedFoldersRef.current = names.map(n => `${parent}\\${n}`)
      }
      const spoken = result?.spoken ?? (result?.success ? 'Done — subfolders created.' : "Couldn't create the subfolders.")
      updMsg(aId, { text: spoken })
      transition('processing')
      transition('speaking')
      await speakResponse(spoken)
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // ── Delete folder / file (with voice confirmation) ────────────────────
    if (sysAction.deleteItem !== undefined) {
      const rawTarget = sysAction.deleteItem.target

      // "delete them/those" → use all paths from last creation
      const isPronouns = !rawTarget || /^(?:them|those|it|that|all)$/i.test(rawTarget.trim())
      let resolvedPaths: string[] = []
      if (isPronouns && lastCreatedFoldersRef.current.length > 0) {
        resolvedPaths = lastCreatedFoldersRef.current
      } else if (rawTarget) {
        resolvedPaths = [rawTarget]
      } else if (lastCreatedFolderRef.current?.path) {
        resolvedPaths = [lastCreatedFolderRef.current.path]
      }

      if (resolvedPaths.length === 0) {
        addMsg('user', transcript)
        const msg = "I'm not sure what to delete. Please say the folder or file name."
        addMsg('assistant', msg)
        transition('speaking')
        await speakResponse(msg)
        transition('idle')
        isRunningRef.current = false
        maybeRestartListening()
        return
      }
      addMsg('user', transcript)
      addMsg('assistant', sysAction.response)
      transition('speaking')
      await speakResponse(sysAction.response)
      // Park the action — use delete-multiple for >1 paths, delete_file for single
      const isBulk = resolvedPaths.length > 1
      pendingConfirmRef.current = {
        tool:   isBulk ? '__delete_multiple__' : 'delete_file',
        params: isBulk ? { paths: resolvedPaths } : { path: resolvedPaths[0] },
        prompt: sysAction.response,
      }
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()
      return
    }

    // ── Fire app / directory launches immediately ─────────────────────────
    // Capture the launch result so we can give real spoken feedback if it
    // fails instead of blindly saying "Done" regardless.
    let appLaunchPromise: Promise<{ success: boolean; message?: string } | null> | null = null
    if (sysAction.app) {
      appLaunchPromise = fetch(`${API_BASE}/api/v1/system/open-app`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ app: sysAction.app }),
      }).then(r => r.json()).catch(() => null)
    }
    if (sysAction.path) {
      // Directory opens are best-effort — no spoken failure needed
      fetch(`${API_BASE}/api/v1/system/open-directory`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: sysAction.path }),
      }).catch(() => {})
    }

    addMsg('user', transcript)
    const aId = addMsg('assistant', sysAction.response)

    const _finish = async (spoken: string) => {
      // If an app launch was attempted, wait for the result and adapt the
      // spoken response — "I couldn't open X" beats a confident lie.
      let finalSpoken = spoken
      if (appLaunchPromise) {
        try {
          const launchResult = await appLaunchPromise
          if (launchResult && !launchResult.success) {
            const appName = sysAction.app ?? 'that app'
            finalSpoken = `I couldn't open ${appName}. ${launchResult.message ?? 'It may not be installed.'}`
          }
        } catch { /* keep original spoken */ }
      }
      updMsg(aId, { text: finalSpoken })
      transition('speaking')
      await speakResponse(finalSpoken)
      transition('idle')
      isRunningRef.current = false
      maybeRestartListening()
    }

    // Plays the holding phrase in the user's selected TTS voice while the data
    // fetch runs in parallel. When both are done we have data ready to speak.
    const _holdAndFetch = async <T>(fetchPromise: Promise<T>): Promise<T> => {
      transition('speaking')
      const [, result] = await Promise.all([
        speakResponse(sysAction.response),
        fetchPromise,
      ])
      transition('processing')
      return result
    }

    if (sysAction.newsQuery) {
      const newsUrl = `https://news.google.com/search?q=${encodeURIComponent(sysAction.newsQuery)}&hl=en`
      const dataFetch = fetch(`${API_BASE}/api/v1/system/news?q=${encodeURIComponent(sysAction.newsQuery)}&limit=5&topic=${sysAction.newsQueryTopic ?? false}`)
        .then(r => r.json()).catch(() => null)
      const d = await _holdAndFetch(dataFetch)
      const spoken = d?.success && d.spoken ? d.spoken : 'I had trouble fetching the news. Check Google News in your browser.'
      if (typeof window !== 'undefined') window.open(newsUrl, '_blank', 'noopener')
      await _finish(spoken)
      return
    }

    if (sysAction.priceQuery) {
      const gUrl = `https://google.com/search?q=${encodeURIComponent(sysAction.priceQuery + ' price today')}`
      const dataFetch = fetch(`${API_BASE}/api/v1/system/price?q=${encodeURIComponent(sysAction.priceQuery)}`)
        .then(r => r.json()).catch(() => null)
      const d = await _holdAndFetch(dataFetch)
      const spoken = d?.success && d.spoken ? d.spoken : `I couldn't fetch the live price for ${sysAction.priceQuery} right now.`
      if (typeof window !== 'undefined') window.open(gUrl, '_blank', 'noopener')
      await _finish(spoken)
      return
    }

    if (sysAction.youtubeQuery) {
      const dataFetch = fetch(`${API_BASE}/api/v1/system/youtube-play`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: sysAction.youtubeQuery }),
      }).then(r => r.json()).catch(() => null)
      const d = await _holdAndFetch(dataFetch)
      let videoUrl = `https://youtube.com/search?q=${encodeURIComponent(sysAction.youtubeQuery)}`
      let msg = 'Here you go!'
      if (d?.url) {
        videoUrl = d.url
        if (d.title) {
          const picks = [`Found it — ${d.title}.`, `Here it is, ${d.title}.`, `Playing ${d.title} now.`, `Got it! ${d.title}, coming right up.`]
          msg = picks[Math.floor(Math.random() * picks.length)]
        }
      }
      if (typeof window !== 'undefined') {
        const ms = mediaSessionRef.current
        const tabAlive = ms.tabRef != null && !ms.tabRef.closed
        if (ms.platform === 'youtube' && ms.isOpen && tabAlive) {
          // Reuse existing YouTube tab — navigate in place, no new tab
          ms.tabRef!.location.href = videoUrl
          ms.tabRef!.focus()
        } else {
          const tab = window.open(videoUrl, '_blank')
          mediaSessionRef.current = { platform: 'youtube', isOpen: true, tabRef: tab, lastQuery: sysAction.youtubeQuery }
        }
        mediaSessionRef.current.lastQuery = sysAction.youtubeQuery
      }
      await _finish(msg)
      return
    }

    if (sysAction.getSystemInfo) {
      const dataFetch = fetch(`${API_BASE}/api/v1/system/system-info`)
        .then(r => r.json()).catch(() => null)
      const d = await _holdAndFetch(dataFetch)
      const spoken = d?.message || 'I had trouble reading your system information right now.'
      await _finish(spoken)
      return
    }

    if (sysAction.url && typeof window !== 'undefined') {
      const tab = window.open(sysAction.url, '_blank')
      // Track media platform opens for session continuity
      if (/youtube\.com/i.test(sysAction.url))
        mediaSessionRef.current = { platform: 'youtube', isOpen: true, tabRef: tab, lastQuery: null }
      else if (/netflix\.com/i.test(sysAction.url))
        mediaSessionRef.current = { platform: 'netflix', isOpen: true, tabRef: tab, lastQuery: null }
      else if (/spotify\.com/i.test(sysAction.url))
        mediaSessionRef.current = { platform: 'spotify', isOpen: true, tabRef: tab, lastQuery: null }
    }

    // ── Clipboard read ────────────────────────────────────────────────────
    if (sysAction.readClipboard) {
      const dataFetch = fetch(`${API_BASE}/api/v1/system/clipboard`)
        .then(r => r.json()).catch(() => null)
      const d = await _holdAndFetch(dataFetch)
      const text = d?.data?.text || d?.text || ''
      const spoken = text ? `Your clipboard has: ${text.slice(0, 200)}` : "Your clipboard appears to be empty."
      await _finish(spoken)
      return
    }

    // ── Clipboard write ───────────────────────────────────────────────────
    if (sysAction.writeClipboard) {
      fetch(`${API_BASE}/api/v1/system/clipboard`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: sysAction.writeClipboard }),
      }).catch(() => {})
      await _finish(sysAction.response)
      return
    }

    // ── Screen reading (vision) ───────────────────────────────────────────
    if (sysAction.readScreen) {
      const dataFetch = fetch(`${API_BASE}/api/v1/system/screen-read`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: transcript }),
      }).then(r => r.json()).catch(() => null)
      const d = await _holdAndFetch(dataFetch)
      const spoken = d?.spoken || d?.message || "I had trouble reading your screen."
      await _finish(spoken)
      return
    }

    // ── Typing automation ─────────────────────────────────────────────────
    if (sysAction.typeText) {
      fetch(`${API_BASE}/api/v1/system/type-text`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: sysAction.typeText }),
      }).catch(() => {})
      await _finish(sysAction.response)
      return
    }

    // ── Window control ────────────────────────────────────────────────────
    if (sysAction.winMinimize) {
      fetch(`${API_BASE}/api/v1/system/window`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'minimize' }),
      }).catch(() => {})
      await _finish(sysAction.response)
      return
    }
    if (sysAction.winMaximize) {
      fetch(`${API_BASE}/api/v1/system/window`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'maximize' }),
      }).catch(() => {})
      await _finish(sysAction.response)
      return
    }
    if (sysAction.winClose) {
      fetch(`${API_BASE}/api/v1/system/window`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'close' }),
      }).catch(() => {})
      await _finish(sysAction.response)
      return
    }
    if (sysAction.winSwitch) {
      fetch(`${API_BASE}/api/v1/system/window`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'switch', title: sysAction.winSwitch }),
      }).catch(() => {})
      await _finish(sysAction.response)
      return
    }

    // ── Reminder creation ─────────────────────────────────────────────────
    if (sysAction.createReminder) {
      const dataFetch = fetch(`${API_BASE}/api/v1/reminders`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: sysAction.createReminder }),
      }).then(r => r.json()).catch(() => null)
      const d = await _holdAndFetch(dataFetch)
      const spoken = d?.spoken || "Got it — I'll remind you."
      await _finish(spoken)
      return
    }

    // ── Gmail read ────────────────────────────────────────────────────────
    if (sysAction.readGmail) {
      const dataFetch = fetch(`${API_BASE}/api/v1/system/gmail/inbox`)
        .then(r => r.json()).catch(() => null)
      const d = await _holdAndFetch(dataFetch)
      const spoken = d?.spoken || d?.message || "I had trouble reading your inbox. Make sure Gmail is connected."
      await _finish(spoken)
      return
    }

    // ── Calendar read ─────────────────────────────────────────────────────
    if (sysAction.readCalendar) {
      const dataFetch = fetch(`${API_BASE}/api/v1/system/calendar/events`)
        .then(r => r.json()).catch(() => null)
      const d = await _holdAndFetch(dataFetch)
      const spoken = d?.spoken || d?.message || "I couldn't read your calendar. Make sure Google Calendar is connected."
      await _finish(spoken)
      return
    }

    // ── Hotkey ────────────────────────────────────────────────────────────
    if (sysAction.desktopHotkey) {
      fetch(`${API_BASE}/api/v1/automation/hotkey`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys: sysAction.desktopHotkey }),
      }).catch(() => {})
      await _finish(sysAction.response)
      return
    }

    // ── Scroll ────────────────────────────────────────────────────────────
    if (sysAction.desktopScroll) {
      fetch(`${API_BASE}/api/v1/automation/scroll`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sysAction.desktopScroll),
      }).catch(() => {})
      await _finish(sysAction.response)
      return
    }

    // ── Workflow trigger ──────────────────────────────────────────────────
    if (sysAction.runWorkflow) {
      const dataFetch = fetch(`${API_BASE}/api/v1/automation/workflow`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trigger: sysAction.runWorkflow }),
      }).then(r => r.json()).catch(() => null)
      const d = await _holdAndFetch(dataFetch)
      const spoken = d?.spoken || d?.message || 'Done!'
      await _finish(spoken)
      return
    }

    await _finish(sysAction.response)
  }

  // ── Public: start listening (called by user tap / wake word) ─────────────

  const startListening = useCallback(() => {
    _startListeningRef.current()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Public: start session (greet, then wait for mic tap) ─────────────────

  const startSession = useCallback(async () => {
    // Discard any stale pending audio from false wakes before activateAudio()'s
    // 50ms timer can replay it (prevents double-greeting on orb clicks).
    clearPendingAudio()

    // Allow takeover from soft states caused by false wakes:
    //   greeting  — false wake waiting to speak
    //   listening — false wake already moved to mic-open loop
    // Hard states (transcribing / processing / speaking) block — user is mid-turn.
    const cur = stateRef.current
    if (cur === 'greeting' || cur === 'listening') {
      if (cur === 'listening') {
        vad.pause()
        isRunningRef.current = false
      }
      stateRef.current = 'idle'
      setSessionState('idle')
    }
    if (stateRef.current !== 'idle') return
    setError(null)
    setMessages([])
    historyRef.current     = []
    sessionIdRef.current   = genUUID()
    activeRef.current      = true
    isRunningRef.current   = false
    isStreamingRef.current = false
    setSessionActive(true)
    window.dispatchEvent(new Event('xyron:session-start'))

    // Greet every time the orb is clicked
    transition('greeting')
    const { mode, voice } = readAssistantSettings()
    const GREETING = buildGreeting(mode)
    addMsg('assistant', GREETING)
    console.log(`[VOICE] greeting with mode=${mode} voice=${voice}`)

    // Orb-click: pointerdown fires before click → audio already activated, no wait.
    // Wake-word: no user gesture yet → wait up to 1500ms for activation, then proceed.
    if (!isAudioActivated()) {
      console.log('[SESSION] waiting for audio activation (wake-word path)...')
      await Promise.race([
        new Promise<void>(r => window.addEventListener('xyron:audio-unlocked', () => r(), { once: true })),
        new Promise<void>(r => setTimeout(r, 1500)),
      ])
      console.log('[SESSION] audio activation wait done, activated now:', isAudioActivated())
    }

    console.log('[SESSION] calling speakResponse for greeting, activated:', isAudioActivated())
    const greetingAbort = new AbortController()
    const greetingTimer = setTimeout(() => {
      console.warn('[SESSION] greeting timeout — aborting fetch to prevent late play')
      greetingAbort.abort()
    }, 10_000)
    try {
      await speakResponse(GREETING, greetingAbort.signal)
    } catch { /* ok */ } finally {
      clearTimeout(greetingTimer)
    }
    if (!activeRef.current) return  // stopped during greeting
    transition('idle')
    console.log('[VOICE] session started — auto-starting listen')
    maybeRestartListening()       // ← kick off first listen cycle automatically
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionState, addMsg, speakResponse, transition, maybeRestartListening])

  // ── Public: stop session ──────────────────────────────────────────────────

  const stopSession = useCallback(() => {
    console.log('[VOICE] session stopped by user')
    activeRef.current      = false
    isStreamingRef.current = false
    isRunningRef.current   = false
    sessionIdRef.current   = ''
    if (sessionTimeoutRef.current) {
      clearTimeout(sessionTimeoutRef.current)
      sessionTimeoutRef.current = null
    }
    abortTask()
    vad.pause()
    if (typeof window !== 'undefined') window.speechSynthesis?.cancel()
    stateRef.current = 'idle'
    setSessionState('idle')
    setSessionActive(false)
    window.dispatchEvent(new Event('xyron:session-end'))
    setMessages([])
    historyRef.current = []
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [abortTask])

  const clearMessages = useCallback(() => {
    setMessages([])
    historyRef.current = []
  }, [])

  // ── Cleanup on unmount ────────────────────────────────────────────────────

  useEffect(() => {
    return () => {
      activeRef.current = false
      if (sessionTimeoutRef.current) clearTimeout(sessionTimeoutRef.current)
      abortTask()
      vad.pause()
      if (typeof window !== 'undefined') window.speechSynthesis?.cancel()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return {
    state:          sessionState,
    messages,
    error,
    startSession,
    stopSession,
    startListening,  // trigger one listen cycle (mic tap / wake word)
    clearMessages,
    isActive:        sessionActive,   // true = session running (even while idle between taps)
    isListening:     sessionState === 'listening',
    followUp,
    dismissFollowUp: () => setFollowUp(null),
    offlineMode,
  }
}
