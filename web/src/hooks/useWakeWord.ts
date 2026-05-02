'use client'

/**
 * Wake word detection via WebSocket + AudioWorklet PCM streaming.
 *
 * Replaces the old MediaRecorder polling approach with a continuous
 * 80ms frame pipeline that feeds the OWW model in real time.
 *
 * Architecture
 * ────────────
 * 1. getUserMedia → AudioContext (16kHz)
 * 2. AudioWorklet (pcm-processor) collects 1280 float32 samples (80ms)
 * 3. WebSocket sends each frame as 5120 bytes (binary)
 * 4. Backend OWW classifier responds with {"type":"wake",...} JSON
 * 5. Hook calls onActivate() and plays the wake beep
 *
 * Fallback (AudioWorklet unavailable)
 * ─────────────────────────────────────
 * If the browser doesn't support AudioWorklet, falls back to
 * ScriptProcessorNode (deprecated but still works in Chrome/Electron).
 *
 * The WS URL is derived from NEXT_PUBLIC_API_URL (ws:// or wss://).
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

const FRAME_SAMPLES = 1280          // 80ms @ 16kHz
const SAMPLE_RATE   = 16_000
const WS_RECONNECT_MS = 2_000       // reconnect delay after disconnect
const WS_TIMEOUT_MS   = 5_000       // how long to wait for "ready" before giving up

function httpToWs(url: string): string {
  return url.replace(/^http:\/\//, 'ws://').replace(/^https:\/\//, 'wss://')
}

function getWsBase(): string {
  const http = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
  return httpToWs(http)
}

function playWakeBeep(): void {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const Cls = window.AudioContext ?? (window as any).webkitAudioContext
    if (!Cls) return
    const ctx  = new Cls() as AudioContext
    const osc  = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.frequency.value = 880
    gain.gain.setValueAtTime(0.25, ctx.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15)
    osc.start(ctx.currentTime)
    osc.stop(ctx.currentTime + 0.15)
    osc.onended = () => ctx.close().catch(() => {})
  } catch { /* ok */ }
}

// ── AudioWorklet processor (inlined as a blob URL) ────────────────────────────
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

export function useWakeWord(onActivate: () => void, enabled: boolean) {
  const activateRef   = useRef(onActivate)
  activateRef.current = onActivate

  const [supported, setSupported]  = useState(false)
  const [listening, setListening]  = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (!enabled) { setListening(false); return }

    let alive       = true
    let wsReady     = false
    let ws: WebSocket | null = null
    let audioCtx: AudioContext | null = null
    let stream: MediaStream | null = null
    let workletUrl: string | null  = null
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let scriptNode: any = null

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

        ws.onopen = () => {
          // wait for {"type":"ready"} from server
        }

        ws.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data as string)
            if (msg.type === 'ready') {
              clearTimeout(timer)
              wsReady = true
              resolve()
            } else if (msg.type === 'wake' && alive) {
              const { model, confidence } = msg
              alive = false
              console.log(`[WakeWord] triggered — model=${model} conf=${confidence.toFixed(3)}`)
              playWakeBeep()
              activateRef.current()
            }
          } catch { /* ignore */ }
        }

        ws.onerror = () => { clearTimeout(timer); reject(new Error('WebSocket error')) }
        ws.onclose = () => { wsReady = false }
      })
    }

    function sendFrame(frame: Float32Array): void {
      if (!ws || ws.readyState !== WebSocket.OPEN || !wsReady) return
      ws.send(frame.buffer)
    }

    // ── Audio pipeline ────────────────────────────────────────────────────

    async function startAudio(): Promise<void> {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation:  true,
            noiseSuppression:  true,
            channelCount:      1,
            sampleRate:        SAMPLE_RATE,
          },
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

      // ── Try AudioWorklet first ────────────────────────────────────────
      if (audioCtx.audioWorklet) {
        try {
          workletUrl = makeWorkletUrl()
          await audioCtx.audioWorklet.addModule(workletUrl)
          const workletNode = new AudioWorkletNode(audioCtx, 'pcm-processor')
          workletNode.port.onmessage = (e: MessageEvent<Float32Array>) => {
            sendFrame(e.data)
          }
          source.connect(workletNode)
          // Don't connect to destination — we just want PCM, no playback
          setListening(true)
          return
        } catch (err) {
          console.warn('[WakeWord] AudioWorklet failed, using ScriptProcessor fallback:', err)
        }
      }

      // ── ScriptProcessorNode fallback ──────────────────────────────────
      const bufSize = 4096
      scriptNode = audioCtx.createScriptProcessor(bufSize, 1, 1)
      let remainder = new Float32Array(0)

      scriptNode.onaudioprocess = (e: AudioProcessingEvent) => {
        const input   = e.inputBuffer.getChannelData(0)
        const merged  = new Float32Array(remainder.length + input.length)
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
      // Open WebSocket
      while (alive) {
        try {
          await openWs()
          break
        } catch (err) {
          console.warn('[WakeWord] WS connect failed, retrying in', WS_RECONNECT_MS, 'ms:', err)
          await new Promise((r) => setTimeout(r, WS_RECONNECT_MS))
          if (!alive) return
        }
      }
      if (!alive) return

      // Start audio pipeline
      await startAudio()
      if (!alive) return

      // Keep WS alive; reconnect if it drops
      while (alive) {
        await new Promise((r) => setTimeout(r, 1_000))
        if (!alive) break

        if (!ws || ws.readyState === WebSocket.CLOSED) {
          console.log('[WakeWord] WS closed, reconnecting...')
          wsReady = false
          try {
            await openWs()
          } catch { /* retry next tick */ }
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
    }
  }, [enabled])

  return { supported, listening }
}
