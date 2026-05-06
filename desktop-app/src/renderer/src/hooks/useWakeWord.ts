/**
 * Wake word detection via WebSocket + AudioWorklet PCM streaming.
 *
 * Replaces the old MediaRecorder/Whisper polling approach with continuous
 * 80ms frame streaming to the backend OWW classifier (~<300ms detection).
 *
 * Desktop-specific: keeps the onWorkMode callback for work-mode phrases
 * (detected via server model name prefix "work_").
 */

import { useEffect, useRef, useState } from 'react'

export const WAKE_PHRASES = [
  'hey xyron', 'hi xyron', 'hy xyron', 'xyron', 'okay xyron', 'ok xyron',
  'zion', 'hi zion', 'hey zion', 'okay zion',
  'cyron', 'hey cyron', 'hi cyron',
  'siren', 'hey siren',
  'xiron', 'hey xiron',
  'zero', 'hey zero',
  'hiron', 'hi ron', 'hey ron',
  'iron', 'hey iron',
  'wake up', 'wakeup', 'wake xyron', 'wakeup xyron',
  'hey assistant', 'hey ai', 'ai operator',
]

const WORK_MODE_MODELS = ['work_mode', 'wakeup_xyron']

const API_BASE      = 'http://localhost:8000'
const WS_WAKE_URL   = `ws://localhost:8000/api/v1/voice/ws/wake`
const FRAME_SAMPLES = 1280
const SAMPLE_RATE   = 16_000
const WS_RECONNECT_MS = 2_000
const WS_TIMEOUT_MS   = 5_000

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

export function useWakeWord(
  onActivate: () => void,
  enabled: boolean,
  onWorkMode?: () => void,
) {
  const activateRef   = useRef(onActivate)
  activateRef.current = onActivate
  const workModeRef   = useRef(onWorkMode)
  workModeRef.current = onWorkMode

  const [supported, setSupported] = useState(false)
  const [listening, setListening] = useState(false)

  useEffect(() => {
    if (!enabled) { setListening(false); return }

    let alive       = true
    let wsReady     = false
    let wakeCooldown = false
    let ws: WebSocket | null = null
    let audioCtx: AudioContext | null = null
    let stream: MediaStream | null = null
    let workletUrl: string | null = null
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let scriptNode: any = null

    function openWs(): Promise<void> {
      return new Promise((resolve, reject) => {
        ws = new WebSocket(WS_WAKE_URL)
        ws.binaryType = 'arraybuffer'

        const timer = setTimeout(() => {
          ws?.close()
          reject(new Error('WS ready timeout'))
        }, WS_TIMEOUT_MS)

        ws.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data as string)
            if (msg.type === 'ready') {
              clearTimeout(timer)
              // Suppress wake events for 2s while OWW warms up on ambient noise
              wakeCooldown = true
              setTimeout(() => { wakeCooldown = false }, 2_000)
              wsReady = true
              resolve()
            } else if (msg.type === 'wake' && alive && !wakeCooldown) {
              wakeCooldown = true
              const { model, confidence } = msg
              console.log(`[WakeWord] triggered — model=${model} conf=${confidence?.toFixed(3)}`)
              playWakeBeep()
              if (WORK_MODE_MODELS.includes(model) && workModeRef.current) {
                workModeRef.current()
              } else {
                activateRef.current()
              }
              // Re-arm after 4s so the WS stays alive for the next wake
              setTimeout(() => {
                wakeCooldown = false
                if (ws?.readyState === WebSocket.OPEN) {
                  ws.send(JSON.stringify({ type: 'reset_cooldown' }))
                }
              }, 4_000)
            }
          } catch { /* ignore */ }
        }

        ws.onerror = () => { clearTimeout(timer); reject(new Error('WS error')) }
        ws.onclose = () => { wsReady = false }
      })
    }

    function sendFrame(frame: Float32Array): void {
      if (!ws || ws.readyState !== WebSocket.OPEN || !wsReady) return
      ws.send(frame.buffer)
    }

    function sendWakeMsg(type: string): void {
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type }))
    }

    const onTtsStart     = () => sendWakeMsg('tts_start')
    const onTtsEnd       = () => sendWakeMsg('tts_end')
    const onSessionStart = () => sendWakeMsg('session_start')
    const onSessionEnd   = () => sendWakeMsg('session_end')
    window.addEventListener('xyron:tts-start',     onTtsStart)
    window.addEventListener('xyron:tts-end',       onTtsEnd)
    window.addEventListener('xyron:session-start', onSessionStart)
    window.addEventListener('xyron:session-end',   onSessionEnd)

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
          const node = new AudioWorkletNode(audioCtx, 'pcm-processor')
          node.port.onmessage = (e: MessageEvent<Float32Array>) => sendFrame(e.data)
          source.connect(node)
          setListening(true)
          return
        } catch (err) {
          console.warn('[WakeWord] AudioWorklet unavailable, falling back:', err)
        }
      }

      // ScriptProcessorNode fallback
      scriptNode = audioCtx.createScriptProcessor(4096, 1, 1)
      let remainder = new Float32Array(0)
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

    async function run(): Promise<void> {
      while (alive) {
        try {
          await openWs()
          break
        } catch (err) {
          console.warn('[WakeWord] WS connect failed, retry in', WS_RECONNECT_MS, 'ms:', err)
          await new Promise((r) => setTimeout(r, WS_RECONNECT_MS))
          if (!alive) return
        }
      }
      if (!alive) return

      await startAudio()
      if (!alive) return

      while (alive) {
        await new Promise((r) => setTimeout(r, 1_000))
        if (!alive) break
        if (!ws || ws.readyState === WebSocket.CLOSED) {
          wsReady = false
          try { await openWs() } catch { /* retry next tick */ }
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
  }, [enabled])

  return { supported, listening }
}
