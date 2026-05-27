import { useState, useRef, useEffect, useCallback } from 'react'

// ── Types ──────────────────────────────────────────────────────────────────────

export interface SimpleVADOptions {
  startOnLoad?: boolean
  onSpeechStart?: () => void
  onSpeechEnd?: (audio: Float32Array) => void
  onVADMisfire?: () => void
  redemptionMs?: number
  minSpeechMs?: number
  preSpeechPadMs?: number
  getStream?: () => Promise<MediaStream>
  // Accepted for API compat with useMicVAD — not used
  positiveSpeechThreshold?: number
  negativeSpeechThreshold?: number
  ortConfig?: unknown
  baseAssetPath?: string
  onnxWASMBasePath?: string
  model?: string
}

// ── Hook ───────────────────────────────────────────────────────────────────────
//
// Pure ScriptProcessorNode energy-based VAD.
// No ONNX, no WASM, no AudioWorklet — works in WebKit2GTK (Tauri/WSL2).
// Drop-in replacement for useMicVAD from @ricky0123/vad-react.
//
// Lifecycle:
//   start()  → getUserMedia + AudioContext (first call); subsequent calls resume
//   pause()  → freeze detection, mic stream stays open for instant resume
//
// Energy thresholds: RMS > 0.020 = speech onset, RMS < 0.010 = silence.

export function useSimpleVAD(options: SimpleVADOptions) {
  const [loading,   setLoading]   = useState(false)
  const [errored,   setErrored]   = useState(false)
  const [listening, setListening] = useState(false)

  // Update callback refs directly during render — no useEffect needed.
  const onSpeechStartRef = useRef(options.onSpeechStart)
  const onSpeechEndRef   = useRef(options.onSpeechEnd)
  const onVADMisfireRef  = useRef(options.onVADMisfire)
  const getStreamRef     = useRef(options.getStream)
  const optsRef          = useRef(options)
  onSpeechStartRef.current = options.onSpeechStart
  onSpeechEndRef.current   = options.onSpeechEnd
  onVADMisfireRef.current  = options.onVADMisfire
  getStreamRef.current     = options.getStream
  optsRef.current          = options

  const streamRef      = useRef<MediaStream | null>(null)
  const audioCtxRef    = useRef<AudioContext | null>(null)
  const processorRef   = useRef<ScriptProcessorNode | null>(null)
  const activeRef      = useRef(false)
  const initializedRef = useRef(false)

  // VAD state — indices into per-frame buffers
  const vadRef = useRef({
    sampleRate:       48000,
    prePadTarget:     2,
    redemptionTarget: 16,
    minSpeechTarget:  5,
    prePad:     [] as Float32Array[],
    speechBufs: [] as Float32Array[],
    isSpeaking:   false,
    speechCount:  0,
    silenceCount: 0,
  })

  const resetVADState = useCallback(() => {
    const v = vadRef.current
    v.prePad     = []
    v.speechBufs = []
    v.isSpeaking  = false
    v.speechCount = 0
    v.silenceCount = 0
  }, [])

  // ── start() ───────────────────────────────────────────────────────────────

  const start = useCallback(async (): Promise<void> => {
    if (activeRef.current) return

    if (!initializedRef.current) {
      setLoading(true)
      try {
        const _rawGetStream = getStreamRef.current
          ?? (() => navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, autoGainControl: true, noiseSuppression: true },
          }))
        // 3s timeout: PULSE_SERVER env var is now set by lib.rs before WebView init,
        // so GStreamer should connect quickly. 3s is generous; fail fast if it hangs.
        const getStream = () => Promise.race([
          _rawGetStream(),
          new Promise<never>((_, reject) =>
            setTimeout(() => reject(new Error('getUserMedia timeout')), 3_000),
          ),
        ])

        const stream = await getStream()
        streamRef.current = stream

        // Request 16 kHz — Whisper's native rate. WebKit may or may not honour it.
        let ctx: AudioContext
        try { ctx = new AudioContext({ sampleRate: 16000 }) }
        catch { ctx = new AudioContext() }
        audioCtxRef.current = ctx

        const rate        = ctx.sampleRate
        const BUF         = 4096
        const bufMs       = (BUF / rate) * 1000
        const opts        = optsRef.current
        const v           = vadRef.current
        v.sampleRate      = rate
        v.prePadTarget    = Math.max(1, Math.ceil((opts.preSpeechPadMs ?? 150)  / bufMs))
        v.redemptionTarget= Math.max(1, Math.ceil((opts.redemptionMs   ?? 1500) / bufMs))
        v.minSpeechTarget = Math.max(1, Math.ceil((opts.minSpeechMs    ?? 400)  / bufMs))

        const POS = 0.020
        const NEG = 0.010

        const source    = ctx.createMediaStreamSource(stream)
        const processor = ctx.createScriptProcessor(BUF, 1, 1)
        processorRef.current = processor

        processor.onaudioprocess = (e: AudioProcessingEvent) => {
          if (!activeRef.current) return
          const vs    = vadRef.current
          const raw   = e.inputBuffer.getChannelData(0)
          const frame = new Float32Array(raw)

          let sq = 0
          for (let i = 0; i < frame.length; i++) sq += frame[i] * frame[i]
          const rms = Math.sqrt(sq / frame.length)

          if (!vs.isSpeaking) {
            vs.prePad.push(frame)
            if (vs.prePad.length > vs.prePadTarget) vs.prePad.shift()
            if (rms >= POS) {
              vs.isSpeaking    = true
              vs.speechCount   = 1
              vs.silenceCount  = 0
              vs.speechBufs    = [...vs.prePad, frame]
              onSpeechStartRef.current?.()
            }
          } else {
            if (rms < NEG) {
              vs.silenceCount++
              vs.speechBufs.push(frame)
              if (vs.silenceCount >= vs.redemptionTarget) {
                const frames   = vs.speechBufs
                const tooShort = vs.speechCount < vs.minSpeechTarget
                vs.isSpeaking   = false
                vs.prePad       = []
                vs.speechBufs   = []
                vs.speechCount  = 0
                vs.silenceCount = 0
                if (tooShort) { onVADMisfireRef.current?.(); return }
                // Concatenate frames into a single buffer
                const total   = frames.reduce((s, f) => s + f.length, 0)
                const merged  = new Float32Array(total)
                let off = 0
                for (const f of frames) { merged.set(f, off); off += f.length }
                // Resample to 16 kHz if needed (linear interpolation)
                let audio16: Float32Array
                if (vs.sampleRate === 16000) {
                  audio16 = merged
                } else {
                  const ratio  = vs.sampleRate / 16000
                  const outLen = Math.floor(merged.length / ratio)
                  audio16 = new Float32Array(outLen)
                  for (let i = 0; i < outLen; i++) {
                    const pos = i * ratio
                    const lo  = Math.floor(pos)
                    const hi  = Math.min(lo + 1, merged.length - 1)
                    audio16[i] = merged[lo] * (1 - (pos - lo)) + merged[hi] * (pos - lo)
                  }
                }
                onSpeechEndRef.current?.(audio16)
              }
            } else {
              vs.silenceCount = 0
              vs.speechCount++
              vs.speechBufs.push(frame)
            }
          }
        }

        source.connect(processor)
        processor.connect(ctx.destination)
        initializedRef.current = true
        console.log('[SimpleVAD] init — rate:', rate, 'bufMs:', bufMs.toFixed(1))
      } catch (err) {
        setLoading(false)
        setErrored(true)
        throw err
      }
      setLoading(false)
    }

    resetVADState()
    activeRef.current = true
    setListening(true)
  }, [resetVADState])

  // ── pause() ───────────────────────────────────────────────────────────────

  const pause = useCallback((): void => {
    activeRef.current = false
    setListening(false)
    resetVADState()
    // Close AudioContext on pause so the caller (wake-word hook) can safely open a new
    // AudioContext.createMediaStreamSource on the shared stream without GStreamer conflict.
    if (processorRef.current) {
      try { processorRef.current.disconnect() } catch {}
      processorRef.current = null
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {})
      audioCtxRef.current = null
    }
    initializedRef.current = false   // force full re-init on next start()
  }, [resetVADState])

  // ── Cleanup on unmount ────────────────────────────────────────────────────

  useEffect(() => {
    return () => {
      activeRef.current = false
      try { processorRef.current?.disconnect() } catch { /* ok */ }
      try { audioCtxRef.current?.close().catch(() => {}) } catch { /* ok */ }
      // Tracks are managed by the shared getMicStream() singleton (stopMicStream).
      // Do NOT stop tracks here — wake-word hook may still be reading from the stream.
    }
  }, [])

  return { start, pause, loading, errored, listening }
}
