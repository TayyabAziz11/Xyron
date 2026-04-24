'use client'

/**
 * Wake word detection using MediaRecorder + Whisper transcription.
 *
 * Uses the same mic + Whisper pipeline as the voice session — works reliably
 * across Chrome, Electron, and any browser that supports getUserMedia.
 *
 * Wake phrases (any of these activate the assistant):
 *   "hey xyron"  |  "xyron"  |  "okay xyron"  |  "wake up"
 *   "hey assistant"  |  "hey ai"  |  "ai operator"
 */

import { useEffect, useRef, useState } from 'react'

export const WAKE_PHRASES = [
  'hey xyron',
  'hi xyron',
  'hy xyron',
  'xyron',
  'okay xyron',
  'ok xyron',
  'wake up',
  'hey assistant',
  'hey ai',
  'ai operator',
]

function matchesWakeWord(transcript: string): boolean {
  const t = transcript.toLowerCase().trim()
  return WAKE_PHRASES.some((p) => t.includes(p))
}

const API_BASE   = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const CLIP_MS    = 1500   // 1.5s gives full phrase capture time
const BETWEEN_MS = 50     // minimal gap so next clip starts almost immediately
const VAD_THRESH = 0.018  // raised to reduce false triggers from background noise

function computeRms(buf: Float32Array): number {
  let sum = 0
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i]
  return Math.sqrt(sum / buf.length)
}

export function useWakeWord(onActivate: () => void, enabled: boolean) {
  const activateRef   = useRef(onActivate)
  activateRef.current = onActivate

  const [supported, setSupported] = useState(false)
  const [listening, setListening] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (!enabled) { setListening(false); return }

    let alive = true

    async function runLoop() {
      let stream: MediaStream | null = null

      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 },
        })
      } catch {
        setSupported(false)
        setListening(false)
        return
      }

      setSupported(true)
      setListening(true)

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
          const chunks: Blob[] = []
          let hasVoice = false

          // Record one clip — VAD polls every 80ms for the full clip duration
          await new Promise<void>((resolve) => {
            const rec = new MediaRecorder(stream!)
            rec.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data) }
            rec.onstop = () => resolve()
            rec.start()

            if (analyser) {
              const timeBuf = new Float32Array(analyser.fftSize)
              // Poll throughout the entire clip — not just the first 12 frames
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
                if (text && matchesWakeWord(text) && alive) {
                  alive = false
                  activateRef.current()
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
