/**
 * Wake word detection using MediaRecorder + Whisper transcription.
 *
 * Uses the same mic + Whisper pipeline as the voice session — guaranteed to
 * work in Electron where webkitSpeechRecognition is flaky (no Google endpoint).
 *
 * Wake phrases (any of these activate the assistant):
 *   "hey xyron"  |  "xyron"  |  "okay xyron"  |  "wake up"
 *   "hey assistant"  |  "hey ai"  |  "ai operator"
 */

import { useEffect, useRef, useState } from 'react'

export const WAKE_PHRASES = [
  // Primary
  'hey xyron', 'hi xyron', 'hy xyron', 'xyron', 'okay xyron', 'ok xyron',
  // Phonetic Whisper variants of "xyron"
  'zion', 'hi zion', 'hey zion', 'okay zion',
  'cyron', 'hey cyron', 'hi cyron',
  'siren', 'hey siren',
  'xiron', 'hey xiron',
  'zero', 'hey zero',
  'hiron', 'hi ron', 'hey ron',
  'iron', 'hey iron',
  // Wake-up phrases
  'wake up', 'wakeup', 'wake xyron', 'wakeup xyron',
  // Fallback
  'hey assistant', 'hey ai', 'ai operator',
]

const WORK_MODE_PHRASES = [
  'time to work', 'work time', "it's work time", 'its work time',
  'wake up work', 'wake up time to work', 'hey buddy wake up',
  "let's get to work", 'lets get to work', "let's work", 'lets work',
  "let's build", 'lets build', "let's code", 'lets code', "let's grind", 'lets grind',
  'ready to work', 'ready to code', 'ready to build',
]

function matchesWorkMode(transcript: string): boolean {
  const t = transcript.toLowerCase().trim()
  return WORK_MODE_PHRASES.some((p) => t.includes(p))
}

function matchesWakeWord(transcript: string): boolean {
  const t = transcript.toLowerCase().trim()
  return WAKE_PHRASES.some((p) => t.includes(p))
}

const API_BASE    = 'http://localhost:8000'
const CLIP_MS     = 900    // 0.9s — fast enough for wake word, low latency
const BETWEEN_MS  = 30     // minimal gap — faster wake word cycle
const VAD_THRESH  = 0.022  // raised — rejects ambient noise while still catching speech

/** Compute RMS of a Float32Array audio buffer (range 0-1) */
function computeRms(buf: Float32Array): number {
  let sum = 0
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i]
  return Math.sqrt(sum / buf.length)
}

export function useWakeWord(onActivate: () => void, enabled: boolean, onWorkMode?: () => void) {
  const activateRef   = useRef(onActivate)
  activateRef.current = onActivate
  const workModeRef   = useRef(onWorkMode)
  workModeRef.current = onWorkMode

  const [supported, setSupported] = useState(false)
  const [listening, setListening] = useState(false)

  useEffect(() => {
    if (!enabled) { setListening(false); return }

    let alive = true

    // ── Main detection loop ─────────────────────────────────────────────────
    async function runLoop() {
      let stream: MediaStream | null = null

      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 },
        })
      } catch {
        // Mic not available — mark unsupported and quit
        setSupported(false)
        setListening(false)
        return
      }

      setSupported(true)
      setListening(true)

      // Build an analyser for quick VAD so we skip silent clips
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const AudioCtxCls = window.AudioContext ?? (window as any).webkitAudioContext
      let analyser: AnalyserNode | null = null
      let ctx: AudioContext | null = null
      if (AudioCtxCls) {
        ctx = new AudioCtxCls() as AudioContext
        analyser = ctx.createAnalyser()
        analyser.fftSize = 512
        ctx.createMediaStreamSource(stream).connect(analyser)
      }

      try {
        while (alive) {
          // Record one clip
          const chunks: Blob[] = []
          let hasVoice = false

          // VAD polls every 80ms for the full clip duration — catches voice at any point
          await new Promise<void>((resolve) => {
            const rec = new MediaRecorder(stream!)
            rec.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data) }
            rec.onstop = () => resolve()
            rec.start()

            if (analyser) {
              const timeBuf = new Float32Array(analyser.fftSize)
              const vadTimer = setInterval(() => {
                analyser!.getFloatTimeDomainData(timeBuf)
                if (computeRms(timeBuf) > VAD_THRESH) hasVoice = true
              }, 80)
              setTimeout(() => {
                clearInterval(vadTimer)
                if (rec.state !== 'inactive') rec.stop()
              }, CLIP_MS)
            } else {
              hasVoice = true
              setTimeout(() => { if (rec.state !== 'inactive') rec.stop() }, CLIP_MS)
            }
          })

          if (!alive) break

          // Fire transcription without awaiting — next clip starts immediately
          // This overlaps Whisper latency with the next recording cycle
          if (hasVoice && chunks.length > 0) {
            const blob = new Blob(chunks, { type: 'audio/webm' })
            ;(async () => {
              try {
                const form = new FormData()
                form.append('audio', blob, 'wake.webm')
                const resp = await fetch(`${API_BASE}/api/v1/voice/transcribe`, {
                  method: 'POST',
                  body: form,
                })
                const data = await resp.json()
                const text: string = data?.data?.text ?? ''
                if (text && alive) {
                  if (matchesWorkMode(text)) {
                    alive = false
                    setTimeout(() => (workModeRef.current ?? activateRef.current)(), 300)
                  } else if (matchesWakeWord(text)) {
                    alive = false
                    setTimeout(() => activateRef.current(), 300)
                  }
                }
              } catch { /* network hiccup — keep going */ }
            })()
          }

          await new Promise((r) => setTimeout(r, BETWEEN_MS))
        }
      } finally {
        stream.getTracks().forEach((t) => t.stop())
        ctx?.close().catch(() => {})
        setListening(false)
      }
    }

    runLoop()

    return () => {
      alive = false
      setListening(false)
    }
  }, [enabled])

  return { supported, listening }
}
