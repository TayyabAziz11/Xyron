/**
 * useVoiceWS — Production-grade realtime voice session over WebSocket.
 *
 * Architecture (ChatGPT Voice / Siri pattern):
 *   1. ONE WebSocket to /api/v1/voice/ws/session — never closes during active session.
 *   2. Shared mic singleton (getMicStream) streams raw PCM frames to backend.
 *   3. Backend handles VAD → STT → LLM → TTS. Frontend is a thin relay.
 *   4. Audio chunks arrive as base64 WAV; played via HTMLAudioElement (Blob URL).
 *   5. Frontend sends "tts_done" when last chunk finishes → backend re-arms VAD.
 *
 * Session flow:
 *   idle → greeting → listening → processing → speaking → listening → (continuous)
 *   Any state → idle on: stopSession(), session_timeout, or WS close.
 *
 * Audio playback uses HTMLAudioElement (same as web/useVoiceSession) for maximum
 * cross-browser / WebKit2GTK / Tauri compatibility. No Web Audio API decodeAudioData.
 *
 * Log tags (structured, with ISO timestamp):
 *   [SESSION_TRANSITION]  [WS_STATE]  [MIC_STATE]
 *   [TTS_START]  [TTS_END]  [STT_END]  [FRONTEND_MOUNT]  [FRONTEND_UNMOUNT]
 *   [AUDIO_UNLOCK_ATTEMPT]  [AUDIO_UNLOCK_SUCCESS]  [AUDIO_UNLOCK_FAILED]
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { SessionState, ConvMessage } from '@/lib/voice-core'
import { getMicStream } from '@/lib/voice-core'
import { readAssistantSettings } from '@/hooks/useAssistantSettings'

// ── Constants ─────────────────────────────────────────────────────────────────

const FRAME_SAMPLES = 1280   // 80ms @ 16kHz
const SAMPLE_RATE   = 16_000

let _sessionWsInstanceCounter = 0

function _ts(): string { return new Date().toISOString() }
function log(tag: string, msg: string): void {
  console.log(`[${tag}] ${_ts()} ${msg}`)
}

function genId(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2)
}

function getWsBase(): string {
  const base = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000')
  return base.replace(/^http:\/\//, 'ws://').replace(/^https:\/\//, 'wss://')
}

// Minimal valid WAV (44-byte header, 0 PCM samples) used to unlock audio autoplay
// on Tauri / WebKit2GTK during the user-gesture window of the session-start click.
function _makeSilentWav(): Blob {
  const buf = new ArrayBuffer(44)
  const v   = new DataView(buf)
  const ws  = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)) }
  ws(0, 'RIFF'); v.setUint32(4, 36, true)
  ws(8, 'WAVE'); ws(12, 'fmt ')
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true)
  v.setUint32(24, 22050, true); v.setUint32(28, 44100, true)
  v.setUint16(32, 2, true); v.setUint16(34, 16, true)
  ws(36, 'data'); v.setUint32(40, 0, true)
  return new Blob([buf], { type: 'audio/wav' })
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useVoiceWS() {
  const [sessionState,  setSessionState]  = useState<SessionState>('idle')
  const [sessionActive, setSessionActive] = useState(false)
  const [messages,      setMessages]      = useState<ConvMessage[]>([])
  const [error,         setError]         = useState<string | null>(null)
  const [followUp,      setFollowUp]      = useState<string | null>(null)

  const stateRef  = useRef<SessionState>('idle')
  const aliveRef  = useRef(false)
  const wsRef     = useRef<WebSocket | null>(null)

  // Mic PCM pipeline
  const micCtxRef  = useRef<AudioContext | null>(null)
  const micProcRef = useRef<ScriptProcessorNode | null>(null)

  // Audio playback pipeline (HTMLAudioElement + Blob URL — same approach as web/useVoiceSession)
  // Serial promise chain — guarantees one chunk plays at a time, no overlap
  const playChainRef      = useRef<Promise<void>>(Promise.resolve())
  const isPlayingRef      = useRef(false)
  const audioUnlockedRef  = useRef(false)
  const pendingMsgRef     = useRef<{ id: string; text: string } | null>(null)
  // Guard: only send tts_done if audio was actually enqueued this turn
  const audioQueuedRef    = useRef(false)

  // Mutex: prevents a second startSession() from running while one is already
  // in-flight (e.g. during the async audio-unlock await). Separate from aliveRef
  // so the guard fires even before the WS is created.
  const sessionConnectingRef = useRef(false)

  // Ref to stopSession so handleMsg can call it without stale closure
  const stopSessionRef = useRef<() => void>(() => {})
  // Stabilization window after wake — flush dirty mic frames before VAD arms
  const stabilizingRef = useRef(false)
  // True while the session-start greeting is being played — drives specialized log tags
  const isGreetingPlayRef = useRef(false)

  // ── State machine ─────────────────────────────────────────────────────────

  const transition = useCallback((next: SessionState) => {
    if (stateRef.current === next) return
    log('SESSION_TRANSITION', `${stateRef.current} → ${next}`)
    stateRef.current = next
    setSessionState(next)
  }, [])

  // ── Message helpers ───────────────────────────────────────────────────────

  const addMsg = useCallback(
    (role: ConvMessage['role'], text: string, status: ConvMessage['status'] = 'done'): string => {
      const id = genId()
      setMessages(prev => [...prev, { id, role, text, timestamp: new Date(), status }])
      return id
    },
    [],
  )

  const updMsg = useCallback((id: string, patch: Partial<ConvMessage>) => {
    setMessages(prev => prev.map(m => m.id === id ? { ...m, ...patch } : m))
  }, [])

  const clearMessages = useCallback(() => {
    setMessages([])
    pendingMsgRef.current = null
  }, [])

  // ── Audio playback pipeline ───────────────────────────────────────────────
  // Uses HTMLAudioElement + Blob URL — same technique as web/useVoiceSession.speakResponse.
  // Avoids Web Audio API decodeAudioData which fails silently in WebKit2GTK / GStreamer.

  const onAllAudioDone = useCallback(() => {
    log('TTS_QUEUE_EMPTY', 'all chunks played')
    isPlayingRef.current = false
    if (pendingMsgRef.current) {
      updMsg(pendingMsgRef.current.id, { status: 'done' })
      pendingMsgRef.current = null
    }
    window.dispatchEvent(new Event('xyron:tts-end'))
    if (!audioQueuedRef.current) {
      log('TTS_DONE_IGNORED', 'no audio queued — skipping tts_done')
      return
    }
    const reason = isGreetingPlayRef.current ? 'greeting_finished' : 'response_finished'
    isGreetingPlayRef.current = false
    audioQueuedRef.current    = false
    if (aliveRef.current && stateRef.current === 'speaking') transition('listening')
    // 150ms drain — WebKit/GStreamer sometimes fires onended before the last sample emits
    setTimeout(() => {
      log('TTS_DONE_SENT', `reason=${reason}`)
      if (wsRef.current?.readyState === WebSocket.OPEN)
        wsRef.current.send(JSON.stringify({ type: 'tts_done' }))
    }, 150)
  }, [transition, updMsg])

  const _playOneChunk = useCallback(async (ab: ArrayBuffer, final: boolean): Promise<void> => {
    return new Promise<void>((resolve) => {
      const { volume } = readAssistantSettings()
      const blob  = new Blob([ab], { type: 'audio/wav' })
      const url   = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.volume = Math.max(0, Math.min(1, volume ?? 1.0))

      let settled = false
      const settle = (reason: string) => {
        if (settled) return; settled = true
        URL.revokeObjectURL(url)
        if (isGreetingPlayRef.current && final) log('GREETING_AUDIO_PLAY', `bytes=${ab.byteLength} reason=${reason}`)
        log('AUDIO_QUEUE_END', `reason=${reason} final=${final}`)
        if (final) onAllAudioDone()
        resolve()
      }

      // Initial safety cap; refined to 2× duration once metadata loads
      let safetyTimer = setTimeout(() => settle('safety_timeout'), 15_000)

      audio.onloadedmetadata = () => {
        clearTimeout(safetyTimer)
        const dur = isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 5
        safetyTimer = setTimeout(() => settle('safety_timeout'), dur * 2000 + 1500)
        log('AUDIO_DECODE_OK', `dur=${dur.toFixed(2)}s bytes=${ab.byteLength} final=${final}`)
        if (isGreetingPlayRef.current && final) log('GREETING_DECODED', `dur_ms=${Math.round(dur * 1000)}`)
      }

      audio.onended = () => { clearTimeout(safetyTimer); settle('ended') }
      audio.onerror = (e) => {
        log('AUDIO_PLAY_ERROR', `onerror=${e} bytes=${ab.byteLength}`)
        clearTimeout(safetyTimer); settle('error')
      }

      if (isGreetingPlayRef.current) log('GREETING_PLAY_START', `bytes=${ab.byteLength}`)
      log('AUDIO_PLAY_START', `bytes=${ab.byteLength} final=${final}`)

      audio.play()
        .then(() => log('AUDIO_PLAY_START', 'play() resolved — audio is playing'))
        .catch((e: unknown) => {
          const name = e instanceof Error ? e.name : String(e)
          log('AUDIO_PLAY_ERROR', `play() rejected: ${name}`)
          clearTimeout(safetyTimer); settle('play_rejected')
        })
    })
  }, [onAllAudioDone])

  const enqueueAudio = useCallback((b64: string, final: boolean): void => {
    log('AUDIO_PACKET_RECEIVED', `b64_len=${b64.length} final=${final}`)
    let ab: ArrayBuffer
    try {
      const bin = atob(b64)
      ab = new ArrayBuffer(bin.length)
      const view = new Uint8Array(ab)
      for (let i = 0; i < bin.length; i++) view[i] = bin.charCodeAt(i)
      log('AUDIO_QUEUE_PUSH', `bytes=${ab.byteLength} final=${final}`)
    } catch (e) {
      log('AUDIO_PLAY_ERROR', `base64 decode: ${e}`)
      if (final) onAllAudioDone()
      return
    }
    audioQueuedRef.current = true
    isPlayingRef.current   = true
    // Append to serial chain — guarantees sequential play with no overlap or interruption
    playChainRef.current = playChainRef.current.then(() => {
      log('AUDIO_QUEUE_POP', `bytes=${ab.byteLength} final=${final}`)
      return _playOneChunk(ab, final)
    })
  }, [_playOneChunk, onAllAudioDone])

  // ── Mic PCM streaming ─────────────────────────────────────────────────────

  const startMic = useCallback(async (): Promise<boolean> => {
    log('MIC_STATE', 'starting')
    try {
      const stream = await getMicStream()

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const Cls = window.AudioContext ?? (window as any).webkitAudioContext
      let ctx: AudioContext
      try { ctx = new Cls({ sampleRate: SAMPLE_RATE }) }
      catch { ctx = new Cls() }
      micCtxRef.current = ctx

      const source    = ctx.createMediaStreamSource(stream)
      const processor = ctx.createScriptProcessor(4096, 1, 1)
      micProcRef.current = processor
      let remainder = new Float32Array(0)
      const rate    = ctx.sampleRate

      processor.onaudioprocess = (e: AudioProcessingEvent) => {
        if (!aliveRef.current || wsRef.current?.readyState !== WebSocket.OPEN) return
        if (stabilizingRef.current) return  // drop frames during post-wake stabilization window
        const raw = e.inputBuffer.getChannelData(0)

        // Resample to 16kHz for backend Whisper
        let samples: Float32Array
        if (rate !== SAMPLE_RATE) {
          const ratio  = rate / SAMPLE_RATE
          const outLen = Math.floor(raw.length / ratio)
          samples      = new Float32Array(outLen)
          for (let i = 0; i < outLen; i++) {
            const pos = i * ratio
            const lo  = Math.floor(pos)
            const hi  = Math.min(lo + 1, raw.length - 1)
            samples[i] = raw[lo] * (1 - (pos - lo)) + raw[hi] * (pos - lo)
          }
        } else {
          samples = new Float32Array(raw)
        }

        // Emit exactly FRAME_SAMPLES (1280) floats per WS message
        const merged = new Float32Array(remainder.length + samples.length)
        merged.set(remainder)
        merged.set(samples, remainder.length)
        let off = 0
        while (off + FRAME_SAMPLES <= merged.length) {
          const frame = merged.slice(off, off + FRAME_SAMPLES)
          wsRef.current!.send(frame.buffer)
          off += FRAME_SAMPLES
        }
        remainder = merged.slice(off)
      }

      source.connect(processor)
      processor.connect(ctx.destination)
      log('MIC_STATE', `active rate=${rate}`)
      return true
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      log('MIC_STATE', `failed: ${msg}`)
      setError(
        /permission|notallowed/i.test(msg)
          ? 'Mic permission denied — allow mic access'
          : /not found|devicenotfound/i.test(msg)
          ? 'No microphone found'
          : `Mic unavailable: ${msg}`,
      )
      return false
    }
  }, [])

  const stopMic = useCallback((): void => {
    log('MIC_STATE', 'stopping')
    if (micProcRef.current) {
      try { micProcRef.current.disconnect() } catch {}
      micProcRef.current = null
    }
    if (micCtxRef.current) {
      micCtxRef.current.close().catch(() => {})
      micCtxRef.current = null
    }
  }, [])

  const stopPlayback = useCallback((): void => {
    // Abandon pending chain — in-flight chunk still completes, nothing new plays
    playChainRef.current  = Promise.resolve()
    isPlayingRef.current  = false
    pendingMsgRef.current = null
  }, [])

  // ── WS message handler ────────────────────────────────────────────────────

  const handleMsg = useCallback((raw: string): void => {
    let msg: Record<string, unknown>
    try { msg = JSON.parse(raw) } catch { return }
    const type = msg.type as string

    switch (type) {

      case 'ack': {
        log('WS_STATE', `ack text="${msg.text}"`)
        const greet = (msg.text as string) || 'Yes?'
        addMsg('system', greet)
        if (msg.audio) {
          transition('speaking')
          window.dispatchEvent(new Event('xyron:tts-start'))
          enqueueAudio(msg.audio as string, true)
        }
        // "listening" message will arrive after backend's deaf window
        break
      }

      case 'listening': {
        log('WS_STATE', 'backend_listening')
        // Transition only if not currently playing audio
        if (stateRef.current !== 'speaking') {
          transition('listening')
        }
        break
      }

      case 'transcript': {
        const text  = (msg.text  as string) ?? ''
        const final = (msg.final as boolean) ?? false
        if (final) {
          log('STT_END', `final="${text.slice(0, 80)}"`)
          addMsg('user', text)
          transition('processing')
        }
        break
      }

      case 'response': {
        const text = (msg.text as string) ?? ''
        if (!pendingMsgRef.current) {
          const id = addMsg('assistant', text, 'processing')
          pendingMsgRef.current = { id, text }
        } else {
          const { id } = pendingMsgRef.current
          const next   = pendingMsgRef.current.text
            ? pendingMsgRef.current.text + ' ' + text
            : text
          pendingMsgRef.current.text = next
          setMessages(prev => prev.map(m => m.id === id ? { ...m, text: next } : m))
        }
        break
      }

      case 'audio': {
        const data  = msg.data  as string
        const final = (msg.final as boolean) ?? false
        if (!data) break
        // Detect greeting: first audio after session connects (state is still 'greeting')
        const isGreeting = stateRef.current === 'greeting'
        if (isGreeting) {
          const byteEst = Math.ceil(data.length * 0.75)
          log('GREETING_CHUNK_RECEIVED', `bytes=${byteEst}`)
          const greetText = (msg.text as string) || ''
          if (greetText) {
            addMsg('assistant', greetText)
            log('CHAT_MESSAGE_ADDED', 'role=assistant type=greeting')
          }
          isGreetingPlayRef.current = true
        }
        if (stateRef.current !== 'speaking') {
          transition('speaking')
          window.dispatchEvent(new Event('xyron:tts-start'))
        }
        enqueueAudio(data, final)
        break
      }

      case 'done': {
        log('WS_STATE', 'utterance_done')
        // Mark message done only if audio has already finished playing
        if (pendingMsgRef.current && !isPlayingRef.current) {
          updMsg(pendingMsgRef.current.id, { status: 'done' })
          pendingMsgRef.current = null
        }
        break
      }

      case 'session_timeout': {
        log('SESSION_TRANSITION', `timeout idle_s=${msg.idle_s}`)
        // Backend will close the WS; ws.onclose handles cleanup
        break
      }

      case 'emotion_state': {
        window.dispatchEvent(new CustomEvent('xyron:emotion', { detail: msg }))
        break
      }

      case 'frontend_action': {
        const action = msg.action as string
        if (action === 'TAKEOVER_START') {
          window.dispatchEvent(new Event('xyron:takeover'))
        } else if (action === 'TAKEOVER_STOP') {
          window.dispatchEvent(new Event('xyron:standdown'))
        }
        break
      }

      case 'error': {
        log('WS_STATE', `server_error: ${msg.message}`)
        setError((msg.message as string) ?? 'Server error')
        break
      }

      case 'ping':
        break

      default:
        log('WS_STATE', `unknown_type="${type}"`)
    }
  }, [addMsg, updMsg, enqueueAudio, transition])

  // ── Session lifecycle ─────────────────────────────────────────────────────

  const stopSession = useCallback((): void => {
    if (!aliveRef.current && !sessionConnectingRef.current) return
    log('SESSION_TRANSITION', 'stopping')
    aliveRef.current             = false
    sessionConnectingRef.current = false
    stabilizingRef.current       = false

    stopMic()
    stopPlayback()

    const ws = wsRef.current
    wsRef.current = null
    if (ws && ws.readyState !== WebSocket.CLOSED) {
      try { ws.close(1000) } catch {}
    }

    transition('stopped')
    setSessionActive(false)
    window.dispatchEvent(new Event('xyron:session-end'))
    // stopped → idle
    setTimeout(() => {
      if (stateRef.current === 'stopped') transition('idle')
    }, 100)
  }, [stopMic, stopPlayback, transition])

  // Keep ref in sync so handleMsg can call it without stale closure
  stopSessionRef.current = stopSession

  const startSession = useCallback(async (): Promise<void> => {
    // ── Mutex: block duplicate concurrent startSession calls ──────────────────
    // sessionConnectingRef covers the gap between startSession() entry and
    // ws.onopen — aliveRef only guards after the WS is created and open.
    if (sessionConnectingRef.current) {
      log('START_SESSION_BLOCKED', 'already connecting — ignoring duplicate call')
      return
    }
    if (aliveRef.current) {
      log('START_SESSION_BLOCKED', 'session already active — ignoring startSession')
      return
    }

    sessionConnectingRef.current = true
    log('START_SESSION_BEGIN', 'entering startSession()')

    try {
      log('SESSION_TRANSITION', 'IDLE → connecting')
      aliveRef.current          = true
      setSessionActive(true)
      setError(null)
      setMessages([])
      setFollowUp(null)
      pendingMsgRef.current     = null
      playChainRef.current      = Promise.resolve()
      isPlayingRef.current      = false
      audioQueuedRef.current    = false
      isGreetingPlayRef.current = false
      transition('greeting')

      // ── Audio autoplay unlock ────────────────────────────────────────────────
      // ROOT BUG FIX: silentAudio.play() in WebKit2GTK without a user gesture can
      // return a Promise that NEVER resolves (not rejects). Awaiting it hangs the
      // entire startSession() forever — new WebSocket() is never reached, and the
      // backend never sees /ws/session. Fix: race with a 500ms timeout so we always
      // proceed regardless of autoplay policy. Mark unlocked even on failure so
      // subsequent wake events skip this block entirely.
      if (!audioUnlockedRef.current) {
        log('AUDIO_UNLOCK_ATTEMPT', 'playing silent WAV — racing with 500ms timeout')
        try {
          const silentUrl   = URL.createObjectURL(_makeSilentWav())
          const silentAudio = new Audio(silentUrl)
          silentAudio.volume = 0
          await Promise.race([
            silentAudio.play(),
            new Promise<void>((_, rej) =>
              setTimeout(() => rej(new Error('autoplay_timeout_500ms')), 500)
            ),
          ])
          URL.revokeObjectURL(silentUrl)
          log('AUDIO_UNLOCK_SUCCESS', 'autoplay unlocked via silent WAV')
        } catch (e) {
          log('AUDIO_UNLOCK_FAILED', `${e} — proceeding anyway`)
        }
        // Always mark unlocked so we never retry this blocking step again.
        audioUnlockedRef.current = true
      }

      // ── Open session WebSocket ───────────────────────────────────────────────
      const url = `${getWsBase()}/api/v1/voice/ws/session`
      log('SESSION_WS_CONNECTING', `url=${url}`)
      const ws  = new WebSocket(url)
      wsRef.current = ws
      const _sessionInstanceId = `session-${++_sessionWsInstanceCounter}`
      console.log(`[SESSION_WS_INSTANCE_CREATED] instance_id=${_sessionInstanceId}`)

      ws.onopen = async () => {
        log('SESSION_WS_OPEN', 'WebSocket /ws/session [connected]')
        sessionConnectingRef.current = false   // WS open — mutex released
        const { voice, speed } = readAssistantSettings()
        ws.send(JSON.stringify({ type: 'config', voice, speed }))
        window.dispatchEvent(new Event('xyron:session-start'))

        // Brief yield: give GStreamer time to settle before attaching mic.
        await new Promise<void>(r => setTimeout(r, 250))
        if (!aliveRef.current) return

        // Start mic but keep stabilization gate open — dirty frames from wake-word
        // pipeline teardown are silently dropped for 400ms.
        stabilizingRef.current = true
        log('AUDIO_STABILIZE_START', 'dropping frames for 400ms post-wake')
        const ok = await startMic()
        if (ok) {
          await new Promise<void>(r => setTimeout(r, 400))
          stabilizingRef.current = false
          log('AUDIO_STABILIZE_END', 'mic buffer flushed — VAD now armed')
        }
        if (!ok && aliveRef.current) {
          aliveRef.current = false
          setSessionActive(false)
          transition('idle')
          try { ws.close() } catch {}
        }
      }

      ws.onmessage = (e) => {
        if (typeof e.data === 'string') {
          // Detect greeting packet for targeted logging
          try {
            const peek = JSON.parse(e.data) as Record<string, unknown>
            if (peek.type === 'audio' && stateRef.current === 'greeting') {
              log('GREETING_PACKET_RECEIVED', `bytes_est=${Math.ceil((peek.data as string ?? '').length * 0.75)}`)
            }
          } catch {}
          handleMsg(e.data)
        }
      }

      ws.onerror = (err) => {
        log('SESSION_WS_ERROR', `WebSocket error: ${err instanceof Event ? 'Event' : String(err)}`)
        if (aliveRef.current) setError('Connection error — retrying…')
      }

      ws.onclose = (e) => {
        log('SESSION_WS_CLOSE', `code=${e.code} reason="${e.reason}" wasClean=${e.wasClean}`)
        sessionConnectingRef.current = false   // ensure mutex released on close
        if (!aliveRef.current) return          // already cleaned up by stopSession()
        aliveRef.current = false
        stopMic()
        stopPlayback()
        transition('idle')
        setSessionActive(false)
        window.dispatchEvent(new Event('xyron:session-end'))
      }

    } catch (err) {
      // Catch any synchronous or async throw so the mutex is always released
      log('START_SESSION_FATAL', `unhandled error: ${err instanceof Error ? err.message : String(err)}`)
      sessionConnectingRef.current = false
      aliveRef.current = false
      setSessionActive(false)
      transition('idle')
    }
  }, [startMic, stopMic, stopPlayback, handleMsg, transition])

  // startListening is a no-op in WS mode: backend VAD continuously listens.
  // Kept for API compatibility with useVoiceSession.
  const startListening = useCallback((): void => {
    log('WS_STATE', 'startListening called — backend VAD handles this automatically')
  }, [])

  // ── Mount / unmount diagnostics ───────────────────────────────────────────

  useEffect(() => {
    log('FRONTEND_MOUNT', 'useVoiceWS mounted')
    return () => {
      log('FRONTEND_UNMOUNT', 'useVoiceWS unmounting')
      aliveRef.current = false
      stopMic()
      stopPlayback()
      try { wsRef.current?.close() } catch {}
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return {
    state:           sessionState,
    messages,
    error,
    startSession:    () => { void startSession() },
    stopSession,
    startListening,
    clearMessages,
    isActive:        sessionActive,
    isListening:     sessionState === 'listening',
    followUp,
    dismissFollowUp: () => setFollowUp(null),
    offlineMode:     false as boolean,
  }
}
