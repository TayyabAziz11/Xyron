'use client'

/**
 * Wake word detection via WebSocket + AudioWorklet PCM streaming.
 *
 * Always-on: runs from mount until unmount, never tied to session lifecycle.
 * The backend session gate (session_active flag) suppresses wake events
 * during an active session — no need to close the WS client-side.
 */

import { useEffect, useRef, useState } from 'react'

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
const WS_TIMEOUT_MS   = 5_000
const WS_POLL_MS      = 200       // check WS health every 200ms (was 1000ms)

function httpToWs(url: string): string {
  return url.replace(/^http:\/\//, 'ws://').replace(/^https:\/\//, 'wss://')
}

function getWsBase(): string {
  const http = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
  return httpToWs(http)
}

const WORKLET_CODE = `
class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this._buf = new Float32Array(${FRAME_SAMPLES})
    this._pos = 0
  }
  process(inputs) {
    const ch = inputs[0]?.[0]
    if (!ch) return true
    for (let i = 0; i < ch.length; i++) {
      this._buf[this._pos++] = ch[i]
      if (this._pos >= ${FRAME_SAMPLES}) {
        this.port.postMessage(this._buf.slice(0))
        this._pos = 0
      }
    }
    return true
  }
}
registerProcessor('pcm-processor', PcmProcessor)
`

function makeWorkletUrl(): string {
  const blob = new Blob([WORKLET_CODE], { type: 'application/javascript' })
  return URL.createObjectURL(blob)
}

// ── Hook ──────────────────────────────────────────────────────────────────────
// Signature: only onActivate — no `enabled` prop.
// Call site should NOT pass a session-lifecycle flag; backend gate handles suppression.

export function useWakeWord(onActivate: () => void) {
  const activateRef   = useRef(onActivate)
  activateRef.current = onActivate

  const [supported, setSupported] = useState(false)
  const [listening, setListening] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined') return

    let alive          = true
    let wsReady        = false
    let wakeCooldown   = false
    let ws: WebSocket | null = null
    let audioCtx: AudioContext | null = null
    let stream: MediaStream | null = null
    let workletUrl: string | null  = null
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
              console.log(`[FLOW] wake_detected — model=${model} conf=${confidence?.toFixed(3)}`)
              activateRef.current()
              // Re-arm after 4s
              setTimeout(() => {
                wakeCooldown = false
                if (ws?.readyState === WebSocket.OPEN) {
                  ws.send(JSON.stringify({ type: 'reset_cooldown' }))
                }
              }, 4_000)
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

    // Forward session/TTS lifecycle to backend gate — backend suppresses wake during session
    const onTtsStart     = () => sendWakeMsg('tts_start')
    const onTtsEnd       = () => sendWakeMsg('tts_end')
    const onSessionStart = () => sendWakeMsg('session_start')
    const onSessionEnd   = () => sendWakeMsg('session_end')
    window.addEventListener('xyron:tts-start',     onTtsStart)
    window.addEventListener('xyron:tts-end',       onTtsEnd)
    window.addEventListener('xyron:session-start', onSessionStart)
    window.addEventListener('xyron:session-end',   onSessionEnd)

    // ── Audio pipeline ────────────────────────────────────────────────────

    async function startAudio(): Promise<void> {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1, sampleRate: SAMPLE_RATE },
        })
      } catch {
        setSupported(false)
        setListening(false)
        return
      }

      setSupported(true)

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const Cls = window.AudioContext ?? (window as any).webkitAudioContext
      if (!Cls) { setListening(false); return }

      audioCtx = new Cls({ sampleRate: SAMPLE_RATE }) as AudioContext
      const source = audioCtx.createMediaStreamSource(stream)

      if (audioCtx.audioWorklet) {
        try {
          workletUrl = makeWorkletUrl()
          await audioCtx.audioWorklet.addModule(workletUrl)
          const workletNode = new AudioWorkletNode(audioCtx, 'pcm-processor')
          workletNode.port.onmessage = (e: MessageEvent<Float32Array>) => sendFrame(e.data)
          source.connect(workletNode)
          setListening(true)
          return
        } catch (err) {
          console.warn('[WAKE] AudioWorklet failed, using ScriptProcessor fallback:', err)
        }
      }

      const bufSize  = 4096
      scriptNode     = audioCtx.createScriptProcessor(bufSize, 1, 1)
      let remainder  = new Float32Array(0)

      scriptNode.onaudioprocess = (e: AudioProcessingEvent) => {
        const input  = e.inputBuffer.getChannelData(0)
        const merged = new Float32Array(remainder.length + input.length)
        merged.set(remainder)
        merged.set(input, remainder.length)
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

      await startAudio()
      if (!alive) return

      // Keepalive: poll every 200ms — reconnect on close OR dead connection
      while (alive) {
        await new Promise((r) => setTimeout(r, WS_POLL_MS))
        if (!alive) break

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
      stream?.getTracks().forEach((t) => t.stop())
      audioCtx?.close().catch(() => {})
      if (workletUrl) URL.revokeObjectURL(workletUrl)
      window.removeEventListener('xyron:tts-start',     onTtsStart)
      window.removeEventListener('xyron:tts-end',       onTtsEnd)
      window.removeEventListener('xyron:session-start', onSessionStart)
      window.removeEventListener('xyron:session-end',   onSessionEnd)
    }
  }, [])  // mount-once — never restarts on session state change

  return { supported, listening }
}
