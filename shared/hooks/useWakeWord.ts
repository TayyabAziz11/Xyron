

/**
 * Wake word detection via WebSocket + ScriptProcessorNode PCM streaming.
 *
 * Always-on: runs from mount until unmount, never tied to session lifecycle.
 * The backend session gate (session_active flag) suppresses wake events
 * during an active session — no need to close the WS client-side.
 */

import { useEffect, useRef, useState } from 'react'
import { getMicStream, stopMicStream } from '@/lib/voice-core'

export const WAKE_PHRASES = [
  'hey xyron', 'hi xyron', 'hy xyron', 'xyron', 'okay xyron', 'ok xyron', 'yo xyron',
  'hey zion', 'hi zion', 'okay zion',
  'hey cyron', 'hi cyron',
  'hey siren', 'hey iron',
  'hey zyron', 'hi zyron',
  'xiron', 'hey xiron',
  'hey zero',
  'hi ron', 'hey ron',
]

const FRAME_SAMPLES   = 1280      // 80ms @ 16kHz
const SAMPLE_RATE     = 16_000
const WS_RECONNECT_MS = 2_000
const WS_TIMEOUT_MS   = 100_000   // 100s — backend waits up to 90s for OWW models on cold start
const WS_POLL_MS      = 200       // check WS health every 200ms (was 1000ms)

let _wakeWsInstanceCounter = 0

function httpToWs(url: string): string {
  return url.replace(/^http:\/\//, 'ws://').replace(/^https:\/\//, 'wss://')
}

function getWsBase(): string {
  const http = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
  return httpToWs(http)
}

// ── Hook ──────────────────────────────────────────────────────────────────────
// Pass enabled=false to defer mic/WS startup until the user explicitly activates
// voice. This prevents getUserMedia from freezing WebKit2GTK on mount.

export function useWakeWord(onActivate: () => void, enabled = true) {
  const activateRef   = useRef(onActivate)
  activateRef.current = onActivate

  const [supported, setSupported] = useState(false)
  const [listening, setListening] = useState(false)

  useEffect(() => {
    if (!enabled) {
      setListening(false)
      return
    }
    if (typeof window === 'undefined') return

    let alive          = true
    let wsReady        = false
    let wakeCooldown   = false
    let sessionActive  = false   // hard lock: no reconnect while voice session is open
    let ws: WebSocket | null = null
    let audioCtx: AudioContext | null = null
    let stream: MediaStream | null = null
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let scriptNode: any = null
    // Heartbeat: track last message from server (backend sends ping every 8s when idle)
    let lastServerMsg  = Date.now()
    // Periodic mic-alive logging
    let frameCount     = 0

    // ── WS helpers ──────────────────────────────────────────────────────────

    function openWs(): Promise<void> {
      return new Promise((resolve, reject) => {
        const url = `${getWsBase()}/api/v1/voice/ws/wake`
        ws = new WebSocket(url)
        ws.binaryType = 'arraybuffer'
        const _wakeInstanceId = `wake-${++_wakeWsInstanceCounter}`
        console.log(`[WAKE_WS_INSTANCE_CREATED] instance_id=${_wakeInstanceId}`)

        const timer = setTimeout(() => {
          ws?.close()
          reject(new Error('WebSocket ready timeout'))
        }, WS_TIMEOUT_MS)

        ws.onopen = () => { /* wait for {"type":"ready"} */ }

        ws.onmessage = (e) => {
          lastServerMsg = Date.now()   // any server message proves the connection is alive
          try {
            const msg = JSON.parse(e.data as string)
            if (msg.type === 'ready') {
              clearTimeout(timer)
              // Suppress events for 2s during OWW model warmup
              wakeCooldown = true
              setTimeout(() => { wakeCooldown = false }, 2_000)
              wsReady = true
              resolve()
            } else if (msg.type === 'wake' && alive && !wakeCooldown) {
              wakeCooldown = true
              const { model, confidence } = msg
              console.log(`[WAKE_EVENT_RECEIVED] model=${model} conf=${confidence?.toFixed(3)}`)
              console.log('[START_SESSION_BEGIN] calling wakeActivate → startSession()')
              activateRef.current()
              // Re-arm after 4s
              setTimeout(() => {
                wakeCooldown = false
                if (ws?.readyState === WebSocket.OPEN) {
                  ws.send(JSON.stringify({ type: 'reset_cooldown' }))
                }
              }, 4_000)
            } else if (msg.type === 'wake_rejected') {
              // Backend heard something OWW flagged, but its Whisper
              // double-check couldn't confirm a wake name — backend already
              // resets its own cooldown for this case, so the very next
              // attempt is heard immediately. No client-side gate to clear
              // here (wakeCooldown was never set for this branch), this is
              // just visibility so a mis-heard first attempt isn't a total
              // mystery in the console.
              console.log(`[WAKE_REJECTED] transcript=${msg.transcript ?? ''} — listening again`)
            } else if (msg.type === 'wake_not_ready') {
              console.log('[WAKE_NOT_READY] Whisper still warming up — try again in a moment')
            }
          } catch { /* ignore */ }
        }

        ws.onerror = () => { clearTimeout(timer); reject(new Error('WebSocket error')) }
        ws.onclose = () => {
          wsReady = false
          console.warn('[WAKE] WS closed — keepalive loop will reconnect')
        }
      })
    }

    function sendFrame(frame: Float32Array): void {
      // Resume AudioContext if browser suspended it (e.g. after TTS plays on a separate context)
      if (audioCtx && audioCtx.state !== 'running') {
        audioCtx.resume().catch(() => {})
        console.log('[WAKE] audio_context_resumed')
      }
      if (!ws || ws.readyState !== WebSocket.OPEN || !wsReady) return
      ws.send(frame.buffer)
      if (++frameCount % 100 === 0) {   // log every ~8s (100 × 80ms)
        console.log(`[WAKE] mic alive — ${frameCount} frames sent, ws=${ws.readyState}, ctx=${audioCtx?.state}`)
      }
    }

    function sendWakeMsg(type: string): void {
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type }))
    }

    // Forward session/TTS lifecycle to backend gate — backend suppresses wake during session.
    // Additionally, close the wake-word AudioContext when a session starts so VAD can
    // safely open its own AudioContext.createMediaStreamSource on the shared stream without
    // GStreamer trying to serve two simultaneous AudioContext consumers (deadlocks WSL2).
    const onTtsStart = () => sendWakeMsg('tts_start')
    const onTtsEnd   = () => sendWakeMsg('tts_end')

    const onSessionStart = () => {
      sessionActive = true
      console.log('[WAKE_SOCKET_PAUSED] voice session started — closing wake WS and freezing reconnect')
      sendWakeMsg('session_start')
      // Hard close the WS — keepalive loop will see sessionActive=true and skip reconnect
      ws?.close()
      wsReady = false
      // Tear down wake-word audio pipeline. VAD opens its own AudioContext next.
      if (scriptNode) { try { scriptNode.disconnect() } catch {} ; scriptNode = null }
      if (audioCtx)   { audioCtx.close().catch(() => {}) ; audioCtx = null }
      setListening(false)
    }

    const onSessionEnd = () => {
      sessionActive = false
      console.log('[WAKE_SOCKET_RESUMED] voice session ended — wake WS will reconnect')
      sendWakeMsg('session_end')
      // Re-open after 600ms — give VAD's AudioContext.close() and GStreamer pipeline
      // time to drain before we attach a new createMediaStreamSource on the shared stream.
      setTimeout(() => {
        if (!alive) return
        console.log('[WAKE] session_end — restarting audio pipeline')
        void startAudio()
      }, 600)
    }

    window.addEventListener('xyron:tts-start',     onTtsStart)
    window.addEventListener('xyron:tts-end',       onTtsEnd)
    window.addEventListener('xyron:session-start', onSessionStart)
    window.addEventListener('xyron:session-end',   onSessionEnd)

    // ── Audio pipeline ────────────────────────────────────────────────────

    async function startAudio(): Promise<void> {
      try {
        // Use the shared singleton stream — prevents two concurrent GStreamer capture
        // pipelines (wake-word + VAD) from deadlocking on WebKit2GTK/WSL2.
        stream = await getMicStream()
      } catch (err) {
        console.warn('[WAKE] getMicStream failed:', err instanceof Error ? err.message : err)
        setSupported(false)
        setListening(false)
        return
      }

      setSupported(true)

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const Cls = window.AudioContext ?? (window as any).webkitAudioContext
      if (!Cls) { setListening(false); return }

      // AudioWorklet silently hangs in WebKit2GTK/WSL2 — use ScriptProcessorNode directly.
      let ctx: AudioContext
      try { ctx = new Cls({ sampleRate: SAMPLE_RATE }) }
      catch { ctx = new Cls() }
      audioCtx = ctx

      const source    = audioCtx.createMediaStreamSource(stream)
      const bufSize   = 4096
      scriptNode      = audioCtx.createScriptProcessor(bufSize, 1, 1)
      let remainder   = new Float32Array(0)
      const ctxRate   = audioCtx.sampleRate

      scriptNode.onaudioprocess = (e: AudioProcessingEvent) => {
        const input = e.inputBuffer.getChannelData(0)

        // Resample to 16kHz so backend always receives correctly-sized 80ms frames.
        let samples: Float32Array
        if (ctxRate !== SAMPLE_RATE) {
          const ratio  = ctxRate / SAMPLE_RATE
          const outLen = Math.floor(input.length / ratio)
          samples = new Float32Array(outLen)
          for (let i = 0; i < outLen; i++) {
            const pos = i * ratio
            const lo  = Math.floor(pos)
            const hi  = Math.min(lo + 1, input.length - 1)
            samples[i] = input[lo] * (1 - (pos - lo)) + input[hi] * (pos - lo)
          }
        } else {
          samples = new Float32Array(input)
        }

        const merged = new Float32Array(remainder.length + samples.length)
        merged.set(remainder)
        merged.set(samples, remainder.length)
        let offset = 0
        while (offset + FRAME_SAMPLES <= merged.length) {
          sendFrame(merged.slice(offset, offset + FRAME_SAMPLES))
          offset += FRAME_SAMPLES
        }
        remainder = merged.slice(offset)
      }

      source.connect(scriptNode)
      scriptNode.connect(audioCtx.destination)
      setListening(true)
    }

    // ── Main loop ─────────────────────────────────────────────────────────

    async function run(): Promise<void> {
      // Initial WS connect with retry
      while (alive) {
        try { await openWs(); break }
        catch (err) {
          console.warn('[WAKE] WS connect failed, retry in', WS_RECONNECT_MS, 'ms:', err)
          await new Promise((r) => setTimeout(r, WS_RECONNECT_MS))
          if (!alive) return
        }
      }
      if (!alive) return

      let audioOk = false
      await Promise.race([
        startAudio().then(() => { audioOk = true }),
        new Promise<void>(r => setTimeout(r, 4_000)),
      ])
      if (!audioOk && alive) {
        console.warn('[WAKE] startAudio timed out — wake word mic unavailable in this environment')
        setSupported(false)
        setListening(false)
      }
      if (!alive) return

      // Keepalive: poll every 200ms — reconnect on close OR dead connection
      // HARD RULE: never reconnect while a voice session is active (sessionActive=true)
      while (alive) {
        await new Promise((r) => setTimeout(r, WS_POLL_MS))
        if (!alive) break

        // Skip ALL reconnect logic during active voice session
        if (sessionActive) continue

        // Resume AudioContext if browser suspended it between polls
        if (audioCtx && audioCtx.state !== 'running') {
          audioCtx.resume().catch(() => {})
        }

        // Dead-connection detection: backend pings every 8s; if >15s silent → zombie connection
        if (ws && ws.readyState === WebSocket.OPEN && Date.now() - lastServerMsg > 15_000) {
          console.warn('[WAKE] heartbeat timeout → reconnecting')
          ws.close()
          wsReady = false
        }

        if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
          console.warn('[WAKE] WS closed → reconnecting...')
          wsReady = false
          lastServerMsg = Date.now()   // reset so reconnect gets a clean heartbeat window
          try { await openWs() } catch { /* retry next poll */ }
        }
      }
    }

    run()

    return () => {
      alive = false
      setListening(false)
      ws?.close()
      stopMicStream()
      audioCtx?.close().catch(() => {})
      window.removeEventListener('xyron:tts-start',     onTtsStart)
      window.removeEventListener('xyron:tts-end',       onTtsEnd)
      window.removeEventListener('xyron:session-start', onSessionStart)
      window.removeEventListener('xyron:session-end',   onSessionEnd)
    }
  }, [enabled])  // re-runs when enabled flips true → starts pipeline; false → cleans up

  return { supported, listening }
}
