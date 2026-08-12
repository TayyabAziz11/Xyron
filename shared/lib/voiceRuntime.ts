/**
 * voiceRuntime — Module-level singleton that owns ALL voice WebSocket state.
 *
 * Architecture:
 *   - WebSocket objects live here, in module scope. React can NEVER destroy them.
 *   - Wake WS: opens once at app startup, persists entire app lifetime.
 *   - Session WS: opened by startSession(), closed only by stopSession() or fatal error.
 *   - Audio: ONE persistent HTMLAudioElement with a URL queue.
 *   - Mic: PCM streaming via ScriptProcessorNode; armed AFTER greeting plays.
 *   - UI subscribes via subscribe() and receives state snapshots.
 *
 * Log tags:
 *   [VOICE_RUNTIME_INSTANCE]  [VOICE_RUNTIME_DUPLICATE]
 *   [SESSION_CREATE]  [SESSION_DESTROY]  [SESSION_DESTROY_REASON]
 *   [SESSION_WS_CONNECT]  [SESSION_WS_DISCONNECT]
 *   [WAKE_WS_CONNECT]  [WAKE_WS_DISCONNECT]
 *   [MIC_START]  [MIC_STOP]  [MIC_STREAM_CREATED]  [MIC_STREAM_DESTROYED]
 *   [VOICE_RUNTIME_HEALTH]
 */

import type { SessionState, ConvMessage } from './voice-core'
import { getMicStream } from './voice-core'
import { readAssistantSettings } from '../hooks/useAssistantSettings'

// ── Constants ──────────────────────────────────────────────────────────────────

// WebSocket uses absolute URL — direct connection works fine in WebKit2GTK/WSL2.
// HTTP fetch uses relative URL in dev (proxied by Vite → backend) because
// WebKit2GTK silently drops cross-origin HTTP fetch in WSL2 dev mode even
// though the CSP and CORS are correctly configured.
const API_BASE_HTTP = import.meta.env.DEV ? '' : 'http://localhost:8000'
const API_BASE_WS   = 'ws://localhost:8000'
const WAKE_URL      = `${API_BASE_WS}/api/v1/voice/ws/wake`
const SESSION_URL   = `${API_BASE_WS}/api/v1/voice/ws/session`

const FRAME_SAMPLES = 1280   // 80ms @ 16kHz
const SAMPLE_RATE   = 16_000
const WAKE_RECONNECT_MAX_MS = 30_000
const HEARTBEAT_INTERVAL_MS = 5_000

// ── Logging helpers ────────────────────────────────────────────────────────────

function ts(): string { return new Date().toISOString() }
function vlog(tag: string, msg: string): void {
  console.log(`[${tag}] ${ts()} ${msg}`)
}

// ── Runtime identity — duplicate detection ─────────────────────────────────────

const _RUNTIME_ID = (typeof crypto !== 'undefined' && crypto.randomUUID)
  ? crypto.randomUUID().slice(0, 8)
  : Math.random().toString(36).slice(2, 10)

// Global registry on window to detect if a second module instance is loaded
// (can happen when the same file is bundled under two import paths).
declare global { interface Window { _xyronRuntimeId?: string } }
if (typeof window !== 'undefined') {
  if (window._xyronRuntimeId && window._xyronRuntimeId !== _RUNTIME_ID) {
    console.error(`[VOICE_RUNTIME_DUPLICATE] ${ts()} TWO runtimes detected! existing=${window._xyronRuntimeId} this=${_RUNTIME_ID}`)
  } else {
    window._xyronRuntimeId = _RUNTIME_ID
    console.log(`[VOICE_RUNTIME_INSTANCE] ${ts()} id=${_RUNTIME_ID}`)
  }
}

// ── Public types ───────────────────────────────────────────────────────────────

export interface VoiceSnapshot {
  sessionState:  SessionState
  sessionActive: boolean
  wakeConnected: boolean
  backendReady:  boolean
  messages:      ConvMessage[]
  error:         string | null
  followUp:      string | null
  offlineMode:   boolean
}

// ── Module state ───────────────────────────────────────────────────────────────

const _snap: VoiceSnapshot = {
  sessionState:  'idle',
  sessionActive: false,
  wakeConnected: false,
  backendReady:  false,
  messages:      [],
  error:         null,
  followUp:      null,
  offlineMode:   false,
}

const _listeners = new Set<(s: VoiceSnapshot) => void>()

function _notify(patch: Partial<VoiceSnapshot>): void {
  Object.assign(_snap, patch)
  const copy = { ..._snap }
  _listeners.forEach(l => { try { l(copy) } catch {} })
}

let _speakingSafetyTimer: ReturnType<typeof setTimeout> | null = null

function _setState(next: SessionState): void {
  if (_snap.sessionState === next) return
  const prev = _snap.sessionState
  vlog('VR_SESSION_STATE', `${prev} → ${next}`)
  _notify({ sessionState: next })

  if (next === 'speaking' || next === 'greeting') {
    vlog('FRONTEND_TTS_START', `state=${next}`)
    vlog('VOICE_SNAPSHOT_SPEAKING', `isSpeaking=true state=${next}`)
    vlog('FRONTEND_SPEAKING_STATE', `${prev} → ${next}`)
    // Safety: if TTS start fires but playback never calls _onAllAudioDone within 8s, force-clear.
    if (_speakingSafetyTimer) clearTimeout(_speakingSafetyTimer)
    _speakingSafetyTimer = setTimeout(() => {
      if (_snap.sessionState === 'speaking' || _snap.sessionState === 'greeting') {
        vlog('FRONTEND_SPEAKING_STATE', `${_snap.sessionState} → listening (2s safety clear — audio done but state stuck)`)
        _setState('listening')
      }
    }, 8_000)
  } else if (prev === 'speaking' || prev === 'greeting') {
    vlog('FRONTEND_SPEAKING_STATE', `${prev} → ${next}`)
    vlog('VOICE_SNAPSHOT_SPEAKING', `isSpeaking=false state=${next}`)
    if (_speakingSafetyTimer) { clearTimeout(_speakingSafetyTimer); _speakingSafetyTimer = null }
  }

  if (next === 'listening') {
    vlog('COMMAND_CENTER_SPEAKING_STATE', `isSpeaking=false → listening`)
  }
}

function _genId(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2)
}

function _addMsg(role: ConvMessage['role'], text: string, status: ConvMessage['status'] = 'done'): string {
  const id = _genId()
  const msg: ConvMessage = { id, role, text, timestamp: new Date(), status }
  _notify({ messages: [..._snap.messages, msg] })
  return id
}

function _updMsg(id: string, patch: Partial<ConvMessage>): void {
  _notify({ messages: _snap.messages.map(m => m.id === id ? { ...m, ...patch } : m) })
}

// ── Module-level audio singleton ───────────────────────────────────────────────
// ── Web Audio API TTS playback ─────────────────────────────────────────────────
// Design note — why we avoid decodeAudioData():
//   WebKitGTK implements decodeAudioData() by creating a GstAppSrc pipeline.
//   On GStreamer ≤1.20 (Ubuntu 22.04), GstAppSrc lacks the 'automatic-eos'
//   property that WebKit tries to set, causing choppy playback and mid-sentence
//   cutoff.  Decoding the PCM WAV manually in JS and building the AudioBuffer
//   directly sidesteps GstAppSrc entirely — no GLib warnings, no dropouts.

interface _TtsEntry { buf: ArrayBuffer; bytes: number; final: boolean }

let _ttsCtx:         AudioContext | null = null
let _ttsQueue:       _TtsEntry[]         = []
let _ttsCurrent:     _TtsEntry | null    = null
let _ttsPlaying      = false
let _ttsOnFinalDone: (() => void) | null = null
let _audioPlaying    = false   // alias kept for health-log references

// ── WAV duration extractor ─────────────────────────────────────────────────────
// Reads only the RIFF/fmt/data chunks — does NOT decode PCM samples.
function _wavDurationMs(buf: ArrayBuffer): number | null {
  try {
    if (buf.byteLength < 44) return null
    const u8   = new Uint8Array(buf)
    const view = new DataView(buf)
    const str4 = (o: number) => String.fromCharCode(u8[o], u8[o+1], u8[o+2], u8[o+3])
    if (str4(0) !== 'RIFF' || str4(8) !== 'WAVE') return null
    let sampleRate = 0, numChannels = 1, bitDepth = 16, dataBytes = 0
    let off = 12
    while (off + 8 <= buf.byteLength) {
      const id   = str4(off)
      const size = view.getUint32(off + 4, true)
      if (id === 'fmt ') {
        numChannels = view.getUint16(off + 10, true)
        sampleRate  = view.getUint32(off + 12, true)
        bitDepth    = view.getUint16(off + 22, true)
      } else if (id === 'data') {
        dataBytes = Math.min(size, buf.byteLength - (off + 8))
        break
      }
      off += 8 + size + (size & 1)
    }
    if (sampleRate === 0 || bitDepth === 0 || numChannels === 0) return null
    const numSamples = dataBytes / (bitDepth / 8) / numChannels
    return (numSamples / sampleRate) * 1000
  } catch { return null }
}

// ── Manual PCM-16 WAV decoder ─────────────────────────────────────────────────
// Handles Kokoro output (24 kHz, mono, s16le) as well as any other PCM-16 WAV.
function _decodeWavToPcm(
  buf: ArrayBuffer,
): { pcm: Float32Array; sampleRate: number; numChannels: number } | null {
  try {
    if (buf.byteLength < 44) return null
    const u8   = new Uint8Array(buf)
    const view = new DataView(buf)
    const str4 = (o: number) => String.fromCharCode(u8[o], u8[o+1], u8[o+2], u8[o+3])

    if (str4(0) !== 'RIFF' || str4(8) !== 'WAVE') return null

    let audioFormat = 0, numChannels = 1, sampleRate = 24000, bitDepth = 16
    let dataOff = -1, dataBytes = 0
    let off = 12

    while (off + 8 <= buf.byteLength) {
      const chunkId   = str4(off)
      const chunkSize = view.getUint32(off + 4, true)
      if (chunkId === 'fmt ') {
        audioFormat = view.getUint16(off + 8,  true)
        numChannels = view.getUint16(off + 10, true)
        sampleRate  = view.getUint32(off + 12, true)
        bitDepth    = view.getUint16(off + 22, true)
      } else if (chunkId === 'data') {
        dataOff   = off + 8
        dataBytes = Math.min(chunkSize, buf.byteLength - dataOff)
        break
      }
      off += 8 + chunkSize + (chunkSize & 1)   // 2-byte alignment
    }

    if (dataOff < 0 || audioFormat !== 1 /* PCM */ || bitDepth !== 16) return null

    const numSamples = Math.floor(dataBytes / 2)
    const pcm        = new Float32Array(numSamples)
    for (let i = 0; i < numSamples; i++) {
      pcm[i] = view.getInt16(dataOff + i * 2, true) / 32768
    }
    return { pcm, sampleRate, numChannels }
  } catch {
    return null
  }
}

// Linear interpolation resampler — good enough for speech TTS
function _resampleLinear(
  src: Float32Array, srcRate: number, dstRate: number,
): Float32Array {
  if (srcRate === dstRate) return src
  const ratio  = srcRate / dstRate
  const outLen = Math.ceil(src.length / ratio)
  const out    = new Float32Array(outLen)
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio
    const lo  = Math.floor(pos)
    const hi  = Math.min(lo + 1, src.length - 1)
    out[i]    = src[lo] + (pos - lo) * (src[hi] - src[lo])
  }
  return out
}

function _getTtsCtx(): AudioContext {
  if (_ttsCtx && _ttsCtx.state !== 'closed') return _ttsCtx
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Cls = window.AudioContext ?? (window as any).webkitAudioContext
  _ttsCtx = new Cls()
  vlog('VR_TTS_CTX_CREATED', `state=${_ttsCtx.state} sampleRate=${_ttsCtx.sampleRate}`)
  if (_ttsCtx.state === 'suspended') {
    _ttsCtx.resume().catch(() => {})
    vlog('VR_TTS_CTX_RESUME', 'resumed suspended AudioContext')
  }
  return _ttsCtx
}

// Track the current HTMLAudioElement so _audioStop() can abort it
let _ttsHtmlAudio: HTMLAudioElement | null = null

async function _ttsPlayNext(): Promise<void> {
  if (_ttsQueue.length === 0) { _ttsPlaying = _audioPlaying = false; return }

  const entry = _ttsQueue.shift()!
  _ttsCurrent = entry
  _ttsPlaying = _audioPlaying = true

  vlog('AUDIO_SRC_SET', `bytes=${entry.bytes} final=${entry.final} remaining=${_ttsQueue.length}`)

  // Compute duration from WAV header so we can set a safe timeout.
  // GStreamer's playbin fires onended reliably; we only fall back to
  // the timeout if the element errors or stalls entirely.
  const durationMs = _wavDurationMs(entry.buf) ?? 30_000
  vlog('AUDIO_WAV_DURATION', `durationMs=${durationMs.toFixed(0)}`)

  const blob = new Blob([entry.buf], { type: 'audio/wav' })
  vlog('VR_AUDIO_BLOB_CREATED', `bytes=${entry.bytes} final=${entry.final}`)
  const url  = URL.createObjectURL(blob)

  const audio = new Audio(url)
  const { volume } = readAssistantSettings()
  audio.volume = Math.max(0, Math.min(1, volume ?? 0.9))
  _ttsHtmlAudio = audio

  let settled = false
  let timeoutHandle: ReturnType<typeof setTimeout> | null = null

  const done = () => {
    if (!settled) {
      settled = true
      if (timeoutHandle !== null) { clearTimeout(timeoutHandle); timeoutHandle = null }
      URL.revokeObjectURL(url)
      _ttsHtmlAudio = null
      if (_ttsCurrent !== entry) return  // invalidated by _audioStop()
      vlog('AUDIO_ONENDED', `bytes=${entry.bytes} final=${entry.final}`)
      _ttsCurrent = null
      _ttsPlaying = _audioPlaying = false
      if (entry.final) {
        const cb = _ttsOnFinalDone; _ttsOnFinalDone = null
        vlog('VR_AUDIO_QUEUE_EMPTY', `final_cb=${!!cb}`)
        if (cb) cb()
      }
      void _ttsPlayNext()
    }
  }

  // Duration-based safety net: durationMs + 5 s cold-start / PulseAudio drain buffer
  timeoutHandle = setTimeout(() => {
    vlog('AUDIO_TIMEOUT', `fired after ${(durationMs + 5000).toFixed(0)}ms — advancing queue`)
    done()
  }, durationMs + 5000)

  audio.onended = () => {
    vlog('VR_AUDIO_PLAY_END', `bytes=${entry.bytes} final=${entry.final}`)
    done()
  }
  audio.onerror = () => {
    const code = audio.error?.code ?? -1
    vlog('VR_AUDIO_PLAY_FAIL', `HTMLAudioElement error code=${code} — falling back to Web Audio API`)
    // HTMLAudioElement failed — try Web Audio API manual decode as fallback
    if (timeoutHandle !== null) { clearTimeout(timeoutHandle); timeoutHandle = null }
    URL.revokeObjectURL(url)
    _ttsHtmlAudio = null
    if (_ttsCurrent !== entry) return
    _ttsPlayWebAudio(entry).then(done).catch(() => done())
  }

  try {
    vlog('VR_AUDIO_PLAY_REQUEST', `bytes=${entry.bytes} dur=${(durationMs / 1000).toFixed(2)}s vol=${audio.volume}`)
    await audio.play()
    vlog('VR_AUDIO_PLAY_START', `bytes=${entry.bytes} dur=${(durationMs / 1000).toFixed(2)}s vol=${audio.volume}`)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    vlog('VR_AUDIO_PLAY_FAIL', `HTMLAudioElement.play() rejected: ${msg} — fallback`)
    if (timeoutHandle !== null) { clearTimeout(timeoutHandle); timeoutHandle = null }
    URL.revokeObjectURL(url)
    _ttsHtmlAudio = null
    if (_ttsCurrent !== entry) { return }
    // Fallback: Web Audio API with manual PCM decode
    try {
      await _ttsPlayWebAudio(entry)
    } catch { /* errors handled inside */ }
    done()
  }
}

// Web Audio API fallback — used when HTMLAudioElement.play() fails
async function _ttsPlayWebAudio(entry: _TtsEntry): Promise<void> {
  const ctx = _getTtsCtx()
  if (ctx.state !== 'running') {
    try {
      await Promise.race([ctx.resume(), new Promise<void>(r => setTimeout(r, 800))])
    } catch { /* ignore */ }
  }

  let audioBuf: AudioBuffer
  const decoded = _decodeWavToPcm(entry.buf)
  if (decoded) {
    const { pcm, sampleRate, numChannels } = decoded
    vlog('AUDIO_WAV_DECODED_FALLBACK', `srcRate=${sampleRate} ch=${numChannels} samples=${pcm.length}`)
    const resampled = _resampleLinear(pcm, sampleRate, ctx.sampleRate)
    const numFrames = Math.floor(resampled.length / numChannels)
    audioBuf = ctx.createBuffer(numChannels, numFrames, ctx.sampleRate)
    if (numChannels === 1) {
      audioBuf.getChannelData(0).set(resampled)
    } else {
      for (let ch = 0; ch < numChannels; ch++) {
        const chData = audioBuf.getChannelData(ch)
        for (let i = 0; i < numFrames; i++) chData[i] = resampled[i * numChannels + ch]
      }
    }
  } else {
    audioBuf = await ctx.decodeAudioData(entry.buf.slice(0))
  }

  const { volume } = readAssistantSettings()
  const gain = ctx.createGain()
  gain.gain.value = Math.max(0, Math.min(1, volume ?? 0.9))
  const source = ctx.createBufferSource()
  source.buffer = audioBuf
  source.connect(gain)
  gain.connect(ctx.destination)

  return new Promise<void>(resolve => {
    const _durMs = (audioBuf.duration || 5) * 1000
    // Safety timeout: WebKitGTK AudioBufferSourceNode.onended may never fire.
    // Resolve after duration + 3s so done() is always called and tts_done is always sent.
    const _safety = setTimeout(() => {
      vlog('AUDIO_WEBAUDIO_SAFETY_TIMEOUT', `${(_durMs + 3000).toFixed(0)}ms — source.onended never fired, resolving`)
      resolve()
    }, _durMs + 3000)
    source.onended = () => {
      clearTimeout(_safety)
      vlog('AUDIO_WEBAUDIO_ONENDED', `dur=${audioBuf.duration.toFixed(2)}s`)
      resolve()
    }
    source.start(0)
    vlog('AUDIO_WEBAUDIO_PLAY', `dur=${audioBuf.duration.toFixed(2)}s ctxState=${ctx.state}`)
  })
}

function _audioEnqueue(b64: string, final: boolean, onFinalDone: () => void): void {
  vlog('SESSION_AUDIO_RECEIVED', `b64_len=${b64.length} final=${final}`)
  let ab: ArrayBuffer
  try {
    const bin = atob(b64)
    ab = new ArrayBuffer(bin.length)
    const view = new Uint8Array(ab)
    for (let i = 0; i < bin.length; i++) view[i] = bin.charCodeAt(i)
    vlog('AUDIO_BINARY_RECEIVED', `bytes=${ab.byteLength}`)
  } catch (e) {
    vlog('AUDIO_DECODE_ERROR', `base64 decode failed: ${e}`)
    if (final) onFinalDone()
    return
  }
  if (final) _ttsOnFinalDone = onFinalDone
  _ttsQueue.push({ buf: ab, bytes: ab.byteLength, final })
  vlog('AUDIO_QUEUE_STATE', `depth=${_ttsQueue.length} playing=${_ttsPlaying}`)
  if (!_ttsPlaying) void _ttsPlayNext()
}

function _audioStop(): void {
  // Invalidate any in-flight play by clearing _ttsCurrent before clearing queue.
  // The done() closure checks (_ttsCurrent !== entry) and no-ops if entry is stale.
  _ttsCurrent     = null
  _ttsQueue       = []
  _ttsPlaying     = _audioPlaying = false
  _ttsOnFinalDone = null
  // Abort active HTMLAudioElement (pauses + lets GStreamer pipeline tear down)
  if (_ttsHtmlAudio) {
    try { _ttsHtmlAudio.pause(); _ttsHtmlAudio.src = '' } catch {}
    _ttsHtmlAudio = null
  }
  // Close Web Audio context so next session gets a fresh one
  if (_ttsCtx) {
    try { _ttsCtx.close() } catch {}
    _ttsCtx = null
  }
}

async function _unlockAudio(): Promise<void> {
  // Web Audio API: AudioContext created after a user gesture starts in 'running'
  // state — no silent-WAV unlock trick needed.  We eagerly create the context
  // here (inside the user-gesture call stack of startSession) so it is never
  // suspended when the first audio frame arrives.
  vlog('AUDIO_UNLOCK_ATTEMPT', 'creating TTS AudioContext inside user-gesture call stack')
  try {
    const ctx = _getTtsCtx()
    if (ctx.state === 'suspended') {
      vlog('AUDIO_UNLOCK_RESUME', 'context suspended on creation — resuming now')
      await Promise.race([ctx.resume(), new Promise<void>(r => setTimeout(r, 1000))])
    }
    vlog('AUDIO_UNLOCK_SUCCESS', `TTS context state=${ctx.state} sampleRate=${ctx.sampleRate}`)
  } catch (e) {
    vlog('AUDIO_UNLOCK_FAILED', `${e} — will retry on first audio frame`)
  }
}

// ── Mic singleton ──────────────────────────────────────────────────────────────

let _micCtx:  AudioContext | null    = null
let _micProc: ScriptProcessorNode | null = null
let _micStabilizing = false
let _micChunksSent  = 0

async function _startMic(): Promise<boolean> {
  _micChunksSent = 0
  vlog('MIC_ARM_START', `runtimeId=${_RUNTIME_ID} sessionAlive=${_sessionAlive} wsState=${_sessionWs?.readyState ?? 'null'} stabilizing=${_micStabilizing}`)
  try {
    const stream = await getMicStream()
    const tracks = stream.getAudioTracks()
    vlog('MIC_STREAM_CREATED', `streamId=${stream.id.slice(0, 8)} tracks=${tracks.length} active=${stream.active}`)
    vlog('MIC_STREAM_TRACK_STATE', `count=${tracks.length} state=${tracks[0]?.readyState ?? 'none'} enabled=${tracks[0]?.enabled ?? 'n/a'} muted=${tracks[0]?.muted ?? 'n/a'} label="${tracks[0]?.label?.slice(0, 40) ?? 'none'}"`)
    {
      const s = tracks[0]?.getSettings?.() ?? {}
      vlog('MIC_STREAM_DIAGNOSTIC',
        `readyState=${tracks[0]?.readyState ?? 'none'} enabled=${tracks[0]?.enabled ?? 'n/a'} ` +
        `muted=${tracks[0]?.muted ?? 'n/a'} label="${tracks[0]?.label?.slice(0, 40) ?? 'none'}" ` +
        `sampleRate=${s.sampleRate ?? 'unknown'} channelCount=${s.channelCount ?? 'unknown'}`)
    }
    if (tracks.length === 0 || tracks[0]?.readyState === 'ended') {
      vlog('MIC_ARM_FAIL', `reason=stale_stream tracks=${tracks.length} readyState=${tracks[0]?.readyState ?? 'none'}`)
      try {
        const { stopMicStream } = await import('./voice-core')
        stopMicStream()
      } catch { /* ok */ }
      return false
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const Cls = window.AudioContext ?? (window as any).webkitAudioContext
    vlog('AUDIO_CONTEXT_DIAGNOSTIC',
      `userAgent="${(navigator.userAgent ?? 'unknown').slice(0, 100)}" ` +
      `platform="${navigator.platform ?? 'unknown'}" ` +
      `constructor=${'AudioContext' in window ? 'AudioContext' : 'webkitAudioContext'} ` +
      `visibilityState=${typeof document !== 'undefined' ? document.visibilityState : 'unknown'} ` +
      `focused=${typeof document !== 'undefined' ? document.hasFocus() : 'unknown'} ` +
      `callOrigin=greeting_done_callback`)
    let ctx: AudioContext
    try { ctx = new Cls({ sampleRate: SAMPLE_RATE }) }
    catch { ctx = new Cls() }
    vlog('AUDIO_CONTEXT_STATE', `stage=created sampleRate=${ctx.sampleRate} state=${ctx.state}`)
    if (ctx.state === 'suspended') {
      try { await Promise.race([ctx.resume(), new Promise<void>(r => setTimeout(r, 500))]) } catch {}
      vlog('AUDIO_CONTEXT_STATE', `stage=after_resume state=${ctx.state}`)
    }
    if (ctx.state !== 'running') {
      vlog('MIC_ARM_FAIL', `reason=ctx_not_running state=${ctx.state} — AudioContext stuck in ${ctx.state}`)
      try { ctx.close() } catch {}
      return false
    }
    _micCtx = ctx

    const source = ctx.createMediaStreamSource(stream)
    const proc   = ctx.createScriptProcessor(4096, 1, 1)
    _micProc = proc
    let remainder = new Float32Array(0)
    const rate    = ctx.sampleRate

    proc.onaudioprocess = (e: AudioProcessingEvent) => {
      if (!_sessionAlive || _sessionWs?.readyState !== WebSocket.OPEN) return
      if (_micStabilizing) return
      const raw = e.inputBuffer.getChannelData(0)

      let samples: Float32Array
      if (rate !== SAMPLE_RATE) {
        const ratio  = rate / SAMPLE_RATE
        const outLen = Math.floor(raw.length / ratio)
        samples      = new Float32Array(outLen)
        for (let i = 0; i < outLen; i++) {
          const pos = i * ratio; const lo = Math.floor(pos)
          const hi  = Math.min(lo + 1, raw.length - 1)
          samples[i] = raw[lo] * (1 - (pos - lo)) + raw[hi] * (pos - lo)
        }
      } else {
        samples = new Float32Array(raw)
      }

      const merged = new Float32Array(remainder.length + samples.length)
      merged.set(remainder); merged.set(samples, remainder.length)
      let off = 0
      while (off + FRAME_SAMPLES <= merged.length) {
        _sessionWs!.send(merged.slice(off, off + FRAME_SAMPLES).buffer)
        _micChunksSent++
        if (_micChunksSent === 1)
          vlog('PCM_FIRST_CHUNK_SENT', `wsState=${_sessionWs?.readyState} ctxState=${ctx.state} rate=${rate} frameBytes=${FRAME_SAMPLES * 4}`)
        if (_micChunksSent % 10 === 0)
          vlog('PCM_CHUNK_COUNT', `count=${_micChunksSent}`)
        off += FRAME_SAMPLES
      }
      remainder = merged.slice(off)
    }

    source.connect(proc)
    proc.connect(ctx.destination)
    vlog('PCM_STREAM_STARTED', `rate=${rate} ctxState=${ctx.state} bufferSize=4096 stabilizing=${_micStabilizing}`)
    vlog('MIC_ARM_SUCCESS', `mic armed — ScriptProcessor connected rate=${rate} ctxState=${ctx.state}`)
    return true
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    vlog('MIC_ARM_FAIL', `reason=exception error="${msg}"`)
    if (/pulseaudio|no microphone/i.test(msg)) {
      _notify({ error: msg })
    }
    return false
  }
}

function _stopMic(): void {
  vlog('MIC_STOP', `runtimeId=${_RUNTIME_ID} chunksSent=${_micChunksSent}`)
  if (_micCtx) {
    vlog('MIC_STREAM_DESTROYED', `runtimeId=${_RUNTIME_ID} ctxState=${_micCtx.state}`)
  }
  _micStabilizing = false
  _micChunksSent  = 0
  if (_micProc) { try { _micProc.disconnect() } catch {}; _micProc = null }
  if (_micCtx)  { _micCtx.close().catch(() => {}); _micCtx = null }
}

// ── onAllAudioDone ─────────────────────────────────────────────────────────────

let _audioQueuedThisTurn = false
let _isGreetingPlay      = false

function _onAllAudioDone(): void {
  vlog('VR_AUDIO_TTS_QUEUE_EMPTY', 'all chunks played')
  if (_pendingMsgId) {
    _updMsg(_pendingMsgId, { status: 'done' })
    _pendingMsgId = null
  }
  window.dispatchEvent(new Event('xyron:tts-end'))
  if (!_audioQueuedThisTurn) {
    vlog('VR_AUDIO_TTS_DONE_IGNORED', 'no audio queued this turn — skipping tts_done and mic arm')
    return
  }
  const wasGreeting      = _isGreetingPlay
  const reason           = wasGreeting ? 'greeting_finished' : 'response_finished'
  _isGreetingPlay        = false
  _audioQueuedThisTurn   = false
  vlog('FRONTEND_TTS_END', `wasGreeting=${wasGreeting} sessionState=${_snap.sessionState}`)
  vlog('VR_AUDIO_PLAYBACK_DONE', `wasGreeting=${wasGreeting} sessionAlive=${_sessionAlive} sessionState=${_snap.sessionState}`)
  // Clear both 'speaking' AND 'greeting' — the ack handler sets 'speaking', the
  // audio handler may leave it as 'greeting' if the audio arrived before the ack.
  if (_sessionAlive && (_snap.sessionState === 'speaking' || _snap.sessionState === 'greeting')) {
    vlog('FRONTEND_SPEAKING_STATE', `${_snap.sessionState} → listening (audio done)`)
    _setState('listening')
  }

  // After non-greeting response: log and re-arm mic if it died mid-session.
  if (!wasGreeting && _sessionAlive) {
    vlog('VR_RESPONSE_AUDIO_DONE', `sessionState=${_snap.sessionState} micProc=${!!_micProc} stabilizing=${_micStabilizing}`)
    if (!_micProc && !_micStabilizing) {
      vlog('VR_MIC_RESTART_AFTER_TTS', 'mic stream not active — re-arming after response audio')
      _micStabilizing = true
      _startMic().then(ok => {
        _micStabilizing = false
        if (ok) vlog('VR_MIC_RESTART_OK', 'mic re-armed after response audio')
        else vlog('VR_MIC_RESTART_FAIL', 'mic re-arm failed — session continues')
      }).catch(e => {
        _micStabilizing = false
        vlog('VR_MIC_RESTART_FAIL', `exception: ${e}`)
      })
    }
  }

  // Arm mic AFTER greeting ends — prevents TTS bleed into STT.
  // 800ms stabilization: GStreamer on WSL2 needs time to drain the wake-word
  // AudioContext pipeline that was closed by onSessionStart before we open a new one.
  if (wasGreeting && _sessionAlive) {
    vlog('VR_MIC_ARM_START', 'greeting done — arming session mic with 800ms stabilization')
    _micStabilizing = true

    // Close the TTS AudioContext so GStreamer releases the pulsesink device before
    // we open pulsesrc for the mic. On WebKitGTK/GStreamer 1.20, having both
    // pulsesink (output) and pulsesrc (input) in separate AudioContexts concurrently
    // can deadlock device enumeration.
    if (_ttsCtx) {
      try { _ttsCtx.close() } catch {}
      _ttsCtx = null
      vlog('VR_TTS_CTX_RELEASED', 'closed TTS AudioContext before mic arm')
    }

    const _doArmMic = (attempt: number) => {
      vlog('MIC_ARM_REQUEST', `attempt=${attempt} sessionAlive=${_sessionAlive} wsState=${_sessionWs?.readyState ?? 'null'} micProc=${!!_micProc}`)
      if (!_sessionAlive) {
        vlog('VR_MIC_ARM_ABORT', `attempt=${attempt} session no longer alive`)
        _micStabilizing = false
        return
      }
      _startMic().then(ok => {
        if (!ok) {
          if (attempt < 2) {
            vlog('VR_MIC_ARM_RETRY', `attempt=${attempt} failed — retrying in 1200ms`)
            setTimeout(() => _doArmMic(attempt + 1), 1200)
          } else {
            vlog('MIC_ARM_FAIL', `reason=all_attempts_exhausted attempt=${attempt} — session continues without mic`)
            _notify({ error: 'Mic unavailable — tap to retry or restart session' })
            _micStabilizing = false
          }
          return
        }
        setTimeout(() => {
          _micStabilizing = false
          vlog('VR_MIC_STABILIZE_END', `attempt=${attempt} mic armed and stable — VAD active stabilizing_cleared=true`)
        }, 800)
      }).catch(e => {
        vlog('MIC_ARM_FAIL', `reason=promise_reject attempt=${attempt} error="${e}"`)
        _micStabilizing = false
      })
    }
    _doArmMic(1)

    // Phase 12 — 5s watchdog: if mic still not active, trigger full recovery
    setTimeout(() => {
      if (_sessionAlive && !_micProc && !_micStabilizing) {
        vlog('MIC_ARM_WATCHDOG_5S', 'mic not active 5s after greeting — triggering recovery')
        _triggerMicRecovery()
      }
    }, 5_000)
  }

  // 150ms drain: GStreamer fires onended slightly before last sample completes
  setTimeout(() => {
    vlog('VR_TTS_DONE_SENT', `reason=${reason} wsState=${_sessionWs?.readyState ?? 'null'}`)
    if (_sessionWs?.readyState === WebSocket.OPEN)
      _sessionWs.send(JSON.stringify({ type: 'tts_done' }))
  }, 150)
}

// ── Session WS message handler ─────────────────────────────────────────────────

let _pendingMsgId:   string | null = null
let _sessionWs:      WebSocket | null = null
let _sessionAlive    = false
let _sessionConnecting = false

function _handleSessionMsg(raw: string): void {
  let msg: Record<string, unknown>
  try { msg = JSON.parse(raw) } catch { return }
  const type = msg.type as string

  switch (type) {
    case 'ack': {
      vlog('VR_SESSION_ACK', `text="${msg.text}"`)
      const greet = (msg.text as string) || 'Yes?'
      _addMsg('system', greet)
      if (msg.audio) {
        _setState('speaking')
        window.dispatchEvent(new Event('xyron:tts-start'))
        _isGreetingPlay      = true
        _audioQueuedThisTurn = true
        _audioEnqueue(msg.audio as string, true, _onAllAudioDone)
      }
      break
    }
    case 'listening': {
      vlog('VR_SESSION_WS_STATE', 'backend_listening')
      if (_snap.sessionState !== 'speaking') {
        _setState('listening')
      } else if (!_ttsPlaying) {
        // Stuck in speaking with no audio — backend confirmed it is ready; recover.
        vlog('VR_LISTENING_UNSTUCK', 'in speaking with no audio playing — forcing listening state')
        _setState('listening')
        // Echo tts_done so backend clears any residual wait state
        if (_sessionWs?.readyState === WebSocket.OPEN) {
          _sessionWs.send(JSON.stringify({ type: 'tts_done' }))
        }
      } else {
        vlog('VR_SESSION_LISTENING_DEFERRED', 'audio still playing — will transition on playback end')
      }
      break
    }
    case 'transcript': {
      const text  = (msg.text  as string) ?? ''
      const final = (msg.final as boolean) ?? false
      if (final) {
        vlog('VR_SESSION_STT_END', `final="${text.slice(0, 80)}"`)
        _addMsg('user', text)
        _setState('processing')
      }
      break
    }
    case 'response': {
      const text = (msg.text as string) ?? ''
      if (!_pendingMsgId) {
        _pendingMsgId = _addMsg('assistant', text, 'processing')
      } else {
        const prev = _snap.messages.find(m => m.id === _pendingMsgId)
        const next = prev ? prev.text + ' ' + text : text
        _updMsg(_pendingMsgId, { text: next })
      }
      break
    }
    case 'audio': {
      const data  = msg.data  as string
      const final = (msg.final as boolean) ?? false
      if (!data) break
      vlog('VR_AUDIO_PACKET_RECEIVED', `b64_len=${data.length} final=${final} sessionState=${_snap.sessionState}`)
      const isGreeting = _snap.sessionState === 'greeting'
      if (isGreeting) {
        vlog('VR_GREETING_AUDIO_RECEIVED', `bytes=${Math.ceil(data.length * 0.75)} final=${final} sessionAlive=${_sessionAlive}`)
        const greetText = (msg.text as string) || ''
        if (greetText) _addMsg('assistant', greetText)
        _isGreetingPlay = true
      }
      if (_snap.sessionState !== 'speaking') {
        _setState('speaking')
        window.dispatchEvent(new Event('xyron:tts-start'))
      }
      _audioQueuedThisTurn = true
      _audioEnqueue(data, final, _onAllAudioDone)
      break
    }
    case 'done': {
      vlog('VR_SESSION_WS_STATE', 'utterance_done')
      if (_pendingMsgId && !_audioPlaying) {
        _updMsg(_pendingMsgId, { status: 'done' })
        _pendingMsgId = null
      }
      break
    }
    case 'session_timeout': {
      vlog('VR_SESSION_TIMEOUT', `idle_s=${msg.idle_s}`)
      break
    }
    case 'emotion_state': {
      window.dispatchEvent(new CustomEvent('xyron:emotion', { detail: msg }))
      break
    }
    case 'frontend_action': {
      const action = msg.action as string
      if (action === 'TAKEOVER_START') window.dispatchEvent(new Event('xyron:takeover'))
      else if (action === 'TAKEOVER_STOP') window.dispatchEvent(new Event('xyron:standdown'))
      break
    }
    case 'mic_required': {
      vlog('MIC_REQUIRED_RECEIVED', `micProc=${!!_micProc} micCtx=${_micCtx?.state ?? 'null'} chunksSent=${_micChunksSent} stabilizing=${_micStabilizing} sessionAlive=${_sessionAlive}`)
      if (!_sessionAlive) break
      if (_micStabilizing) {
        vlog('VR_MIC_REARM_SKIP', 'already stabilizing — skipping restart')
        break
      }
      // Always restart mic — backend sends mic_required when post-TTS silence is detected.
      // mic may be "active" (micProc != null) but producing a silent/stuck stream after
      // audio playback; stop + restart to get a fresh stream from the OS.
      vlog('VR_MIC_REARM_START', `force-restarting mic (was active=${!!_micProc})`)
      _micStabilizing = true
      _stopMic()
      setTimeout(() => {
        if (!_sessionAlive) { _micStabilizing = false; return }
        _startMic().then(ok => {
          if (!ok) {
            vlog('VR_MIC_REARM_FAIL', 'mic restart failed after backend request')
            _notify({ error: (msg.message as string) ?? 'Mic unavailable — check permissions' })
            _micStabilizing = false
          } else {
            setTimeout(() => {
              _micStabilizing = false
              vlog('VR_MIC_REARM_SUCCESS', 'mic restarted by backend request — VAD active')
            }, 800)
          }
        }).catch(e => {
          vlog('VR_MIC_REARM_FAIL', `restart error: ${e}`)
          _micStabilizing = false
        })
      }, 200)
      break
    }
    case 'not_ready': {
      // Backend rejected session — boot not complete yet. Clear session state immediately.
      vlog('SESSION_NOT_READY', `state=${msg.state} reason="${msg.reason}"`)
      _sessionAlive      = false
      _sessionConnecting = false
      _stopMic()
      _setState('idle')
      _notify({ sessionActive: false, error: `Xyron is preparing… (${msg.state as string})` })
      vlog('FRONTEND_READY_STATE', `ready=false state=${msg.state as string} — resuming poll`)
      if (!_readyPollTimer) _pollReady()
      break
    }
    case 'error': {
      vlog('VR_SESSION_ERROR', `${msg.message}`)
      _notify({ error: (msg.message as string) ?? 'Server error' })
      break
    }
    case 'ping': break
    default: vlog('VR_SESSION_UNKNOWN_TYPE', `"${type}"`)
  }
}

// ── Phase 12: Mic arm recovery ─────────────────────────────────────────────────

function _triggerMicRecovery(): void {
  vlog('MIC_RECOVERY_START', `micProc=${!!_micProc} stabilizing=${_micStabilizing}`)
  _micStabilizing = true
  _stopMic()  // tear down stale stream before re-arming
  setTimeout(() => {
    if (!_sessionAlive) { _micStabilizing = false; return }
    _startMic().then(ok => {
      _micStabilizing = false
      if (ok) {
        vlog('MIC_RECOVERY_SUCCESS', 'mic re-armed by 5s watchdog')
      } else {
        vlog('MIC_RECOVERY_FAIL', 'mic recovery failed — showing user error')
        _notify({ error: 'Mic unavailable. Check permissions or restart session.' })
      }
    }).catch(e => {
      _micStabilizing = false
      vlog('MIC_RECOVERY_FAIL', `exception: ${e}`)
    })
  }, 300)
}

// ── Wake WS singleton ──────────────────────────────────────────────────────────

let _wakeWs:         WebSocket | null = null
let _wakeStarted     = false
let _wakeRetryMs     = 1_000
let _wakeRetryTimer: ReturnType<typeof setTimeout> | null = null
let _wakeCooldown    = false
let _wakeActivateCb: (() => void) | null = null
let _wakeStreamAlive = false
let _wakeAudioCtx:   AudioContext | null = null
let _wakeAudioProc:  ScriptProcessorNode | null = null

// ── Backend readiness polling ──────────────────────────────────────────────────
let _backendReady    = false
let _readyPollTimer: ReturnType<typeof setTimeout> | null = null

function _pollReady(): void {
  if (_backendReady) return
  const url = `${API_BASE_HTTP}/api/v1/ready`
  vlog('FRONTEND_READY_REQUEST', `url=${url}`)
  fetch(url)
    .then(r => {
      vlog('FRONTEND_READY_RESPONSE', `status=${r.status} ok=${r.ok}`)
      return r.json()
    })
    .then((body: { ready?: boolean; core_ready?: boolean; full_ready?: boolean; state?: string; blockers?: string[] }) => {
      const prev = _backendReady
      const gateOpen = body.core_ready === true || body.ready === true
      vlog('FRONTEND_READY_RESPONSE', `core_ready=${body.core_ready} full_ready=${body.full_ready} state=${body.state ?? 'unknown'} blockers=${JSON.stringify(body.blockers ?? [])}`)
      if (gateOpen) {
        _backendReady = true
        if (_readyPollTimer) { clearTimeout(_readyPollTimer); _readyPollTimer = null }
        vlog('FRONTEND_READY_STATE', `prev=${prev} → ready=true state=${body.state ?? 'CORE_READY'} — voice sessions and wake word now enabled`)
        _notify({ backendReady: true, error: null })
        vlog('VOICE_SNAPSHOT_READY', 'backendReady=true — all subscribers notified')
        vlog('WAKE_CONNECT_ALLOWED_READY', 'backend CORE_READY — initiating wake WS connection')
        _connectWake()
      } else {
        vlog('FRONTEND_READY_STATE', `ready=false state=${body.state ?? 'unknown'} blockers=${JSON.stringify(body.blockers ?? [])} — retrying in 2s`)
        _readyPollTimer = setTimeout(_pollReady, 2_000)
      }
    })
    .catch((err: unknown) => {
      vlog('FRONTEND_READY_ERROR', `fetch_error=${String(err)} — retrying in 3s`)
      _readyPollTimer = setTimeout(_pollReady, 3_000)
    })
}

function _connectWake(): void {
  if (_wakeWs?.readyState === WebSocket.OPEN || _wakeWs?.readyState === WebSocket.CONNECTING) {
    console.warn(`[WAKE_WS_DUPLICATE_BLOCKED] ${ts()} already connected/connecting`)
    return
  }
  vlog('WAKE_WS_CONNECT', `url=${WAKE_URL} runtimeId=${_RUNTIME_ID}`)
  const ws = new WebSocket(WAKE_URL)
  ws.binaryType = 'arraybuffer'
  _wakeWs = ws

  const timer = setTimeout(() => {
    vlog('WAKE_WS_CLOSE_REQUESTED', `reason=5s_no_ready readyState=${ws.readyState}`)
    vlog('VR_WAKE_TIMEOUT', 'no ready in 5s — closing')
    ws.close()
  }, 5_000)

  ws.onopen = () => {
    vlog('VR_WAKE_WS_STATE', 'OPEN — waiting for ready frame')
  }

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data as string)
      if (msg.type === 'ready') {
        clearTimeout(timer)
        _wakeRetryMs = 1_000
        _notify({ wakeConnected: true })
        vlog('VR_WAKE_READY', `models=${JSON.stringify(msg.models)}`)
        _wakeCooldown = true
        setTimeout(() => { _wakeCooldown = false }, 2_000)
        if (!_wakeStreamAlive) _startWakeAudio()
      } else if (msg.type === 'wake' && !_wakeCooldown) {
        _wakeCooldown = true
        vlog('VR_WAKE_TRIGGERED', `model=${msg.model} conf=${(msg.confidence as number)?.toFixed?.(3) ?? '?'}`)
        _wakeActivateCb?.()
        setTimeout(() => {
          _wakeCooldown = false
          if (_wakeWs?.readyState === WebSocket.OPEN)
            _wakeWs.send(JSON.stringify({ type: 'reset_cooldown' }))
        }, 4_000)
      } else if (msg.type === 'ping') {
        // Reply so WebKit2GTK doesn't idle-timeout the WS due to client silence
        if (_wakeWs?.readyState === WebSocket.OPEN)
          _wakeWs.send(JSON.stringify({ type: 'pong' }))
      }
    } catch {}
  }

  ws.onerror = () => { clearTimeout(timer) }

  ws.onclose = (e) => {
    clearTimeout(timer)
    vlog('WAKE_WS_CLOSED', `code=${e.code} reason="${e.reason}" wasClean=${e.wasClean} backendReady=${_backendReady} wakeStarted=${_wakeStarted}`)
    vlog('WAKE_WS_DISCONNECT', `code=${e.code} reason="${e.reason}" wasClean=${e.wasClean} runtimeId=${_RUNTIME_ID}`)
    if (!e.wasClean) console.trace('[WAKE_WS_DISCONNECT_TRACE]')
    _notify({ wakeConnected: false })
    if (_wakeStarted && _backendReady) {
      _wakeRetryTimer = setTimeout(() => {
        _wakeRetryMs = Math.min(_wakeRetryMs * 2, WAKE_RECONNECT_MAX_MS)
        _connectWake()
      }, _wakeRetryMs)
    }
  }
}

function _sendWakeMsg(type: string): void {
  if (_wakeWs?.readyState === WebSocket.OPEN)
    _wakeWs.send(JSON.stringify({ type }))
}

async function _startWakeAudio(): Promise<void> {
  if (_wakeStreamAlive) return
  try {
    const stream = await getMicStream()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const Cls = window.AudioContext ?? (window as any).webkitAudioContext
    const audioCtx: AudioContext = new Cls({ sampleRate: SAMPLE_RATE })
    _wakeAudioCtx = audioCtx
    const source = audioCtx.createMediaStreamSource(stream)
    let remainder = new Float32Array(0)

    if (audioCtx.audioWorklet) {
      const code = `
        class PcmProcessor extends AudioWorkletProcessor {
          constructor() { super(); this._buf = new Float32Array(${FRAME_SAMPLES}); this._pos = 0 }
          process(inputs) {
            const ch = inputs[0]?.[0]; if (!ch) return true
            for (let i = 0; i < ch.length; i++) {
              this._buf[this._pos++] = ch[i]
              if (this._pos >= ${FRAME_SAMPLES}) { this.port.postMessage(this._buf.slice(0)); this._pos = 0 }
            }
            return true
          }
        }
        registerProcessor('pcm-processor', PcmProcessor)`
      const blob = new Blob([code], { type: 'application/javascript' })
      const url  = URL.createObjectURL(blob)
      try {
        await audioCtx.audioWorklet.addModule(url)
        URL.revokeObjectURL(url)
        const node = new AudioWorkletNode(audioCtx, 'pcm-processor')
        node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
          if (_wakeWs?.readyState === WebSocket.OPEN) _wakeWs.send(ev.data.buffer)
        }
        source.connect(node)
        _wakeStreamAlive = true
        vlog('VR_WAKE_AUDIO', 'AudioWorklet streaming started')
        return
      } catch (err) {
        vlog('VR_WAKE_AUDIO', `AudioWorklet failed, falling back: ${err}`)
        URL.revokeObjectURL(url)
      }
    }

    const proc = audioCtx.createScriptProcessor(4096, 1, 1)
    _wakeAudioProc = proc
    proc.onaudioprocess = (e: AudioProcessingEvent) => {
      const input  = e.inputBuffer.getChannelData(0)
      const merged = new Float32Array(remainder.length + input.length)
      merged.set(remainder); merged.set(input, remainder.length)
      let off = 0
      while (off + FRAME_SAMPLES <= merged.length) {
        if (_wakeWs?.readyState === WebSocket.OPEN)
          _wakeWs.send(merged.slice(off, off + FRAME_SAMPLES).buffer)
        off += FRAME_SAMPLES
      }
      remainder = merged.slice(off)
    }
    source.connect(proc)
    proc.connect(audioCtx.destination)
    _wakeStreamAlive = true
    vlog('VR_WAKE_AUDIO', 'ScriptProcessor streaming started')
  } catch (err) {
    vlog('VR_WAKE_AUDIO', `failed to start: ${err}`)
  }
}

// ── Heartbeat ──────────────────────────────────────────────────────────────────

let _heartbeatTimer: ReturnType<typeof setInterval> | null = null

function _startHeartbeat(): void {
  if (_heartbeatTimer) return
  _heartbeatTimer = setInterval(() => {
    const wakeState = _wakeWs?.readyState ?? -1
    const sessState = _sessionWs?.readyState ?? -1
    const wsStateStr = (n: number) =>
      n === WebSocket.CONNECTING ? 'CONNECTING'
      : n === WebSocket.OPEN     ? 'OPEN'
      : n === WebSocket.CLOSING  ? 'CLOSING'
      : n === WebSocket.CLOSED   ? 'CLOSED' : 'NONE'
    vlog('VOICE_RUNTIME_HEALTH',
      `wake=${wsStateStr(wakeState)} session=${wsStateStr(sessState)} ` +
      `audioQueue=${_ttsQueue.length} playing=${_ttsPlaying} ` +
      `sessionState=${_snap.sessionState} micStabilizing=${_micStabilizing}`)
  }, HEARTBEAT_INTERVAL_MS)
}

// ── Public API ─────────────────────────────────────────────────────────────────

export function getSnapshot(): VoiceSnapshot {
  return { ..._snap }
}

export function subscribe(listener: (s: VoiceSnapshot) => void): () => void {
  _listeners.add(listener)
  listener({ ..._snap })
  return () => { _listeners.delete(listener) }
}

/**
 * Called by React safety-net polling when it independently confirms backend READY.
 * No-op if already ready. Safe to call multiple times.
 */
export function triggerBackendReady(): void {
  if (_backendReady) return
  _backendReady = true
  if (_readyPollTimer) { clearTimeout(_readyPollTimer); _readyPollTimer = null }
  vlog('VOICE_RUNTIME_READY', 'triggerBackendReady() — set by React safety-net poll')
  vlog('FRONTEND_READY_STATE', 'prev=false → ready=true (external trigger)')
  _notify({ backendReady: true, error: null })
  vlog('WAKE_CONNECT_ALLOWED_READY', 'backend READY — initiating wake WS connection')
  _connectWake()
}

/** Call ONCE at app startup. Never again. */
export function initWake(onActivate: () => void): void {
  if (_wakeStarted) {
    vlog('VR_WAKE_INIT', 'already started — updating activate callback')
    _wakeActivateCb = () => {
      if (!_backendReady) {
        vlog('WAKE_START_BLOCKED_NOT_READY', 'backend not ready — ignoring wake trigger')
        return
      }
      onActivate()
    }
    return
  }
  _wakeStarted    = true
  _wakeActivateCb = () => {
    if (!_backendReady) {
      vlog('WAKE_START_BLOCKED_NOT_READY', 'backend not ready — ignoring wake trigger')
      return
    }
    onActivate()
  }
  vlog('VR_WAKE_INIT', 'starting wake WS for first time')
  vlog('WAKE_START_BLOCKED_NOT_READY', 'wake WS gated — will connect only after backend READY')
  // Poll backend readiness — wake WS is started by _pollReady() once ready=true
  vlog('FRONTEND_READY_POLL_START', `polling ${API_BASE_HTTP}/api/v1/ready until backend is up`)
  _pollReady()
  _startHeartbeat()
  // _connectWake() is NOT called here — _pollReady() calls it after backend READY

  // Trigger GStreamer device discovery early so it completes before the first
  // getUserMedia call. WebKitGTK discovers PulseAudio sources asynchronously;
  // without this warm-up, getMicStream() may see 0 devices on its first attempt.
  if (typeof navigator !== 'undefined' && navigator.mediaDevices) {
    navigator.mediaDevices.enumerateDevices().then(devs => {
      const n = devs.filter(d => d.kind === 'audioinput').length
      vlog('VR_DEVICE_WARMUP', `early enumerate found ${n} audio input device(s)`)
    }).catch(() => { /* non-fatal */ })
  }

  // Forward session events to wake WS so it can gate itself
  window.addEventListener('xyron:tts-start',     () => _sendWakeMsg('tts_start'))
  window.addEventListener('xyron:tts-end',       () => _sendWakeMsg('tts_end'))
  window.addEventListener('xyron:session-start', () => _sendWakeMsg('session_start'))
  window.addEventListener('xyron:session-end',   () => _sendWakeMsg('session_end'))
}

export async function startSession(): Promise<void> {
  if (!_backendReady) {
    vlog('VOICE_START_BLOCKED_NOT_READY', 'backend not ready — session start suppressed')
    _notify({ error: 'Xyron is still preparing. Please wait a moment...' })
    return
  }
  vlog('VOICE_START_ALLOWED_READY', 'backend ready — proceeding with session start')
  if (_sessionConnecting) {
    vlog('VR_SESSION_DUPLICATE_BLOCKED', `reason=connecting state=${_snap.sessionState}`)
    return
  }
  if (_sessionAlive) {
    vlog('VR_SESSION_DUPLICATE_BLOCKED', `reason=alive state=${_snap.sessionState}`)
    return
  }
  _sessionConnecting = true
  vlog('SESSION_CREATE', `id=${_RUNTIME_ID} entering startSession()`)

  // Reset per-session state
  _audioStop()
  _audioQueuedThisTurn = false
  _isGreetingPlay      = false
  _pendingMsgId        = null
  _notify({
    sessionState:  'greeting',
    sessionActive: true,
    messages:      [],
    error:         null,
    followUp:      null,
  })
  _sessionAlive = true

  try {
    await _unlockAudio()

    vlog('VR_SESSION_WS_CONNECTING', SESSION_URL)
    const ws = new WebSocket(SESSION_URL)
    ws.binaryType = 'arraybuffer'
    _sessionWs = ws

    ws.onopen = () => {
      vlog('SESSION_WS_CONNECT', `url=${SESSION_URL} runtimeId=${_RUNTIME_ID}`)
      _sessionConnecting = false
      const { voice, speed } = readAssistantSettings()
      ws.send(JSON.stringify({ type: 'config', voice, speed }))
      window.dispatchEvent(new Event('xyron:session-start'))
      vlog('VR_MIC_DEFER_PENDING', 'mic will arm after greeting playback ends')
    }

    ws.onmessage = (e) => {
      if (typeof e.data === 'string') {
        try {
          const peek = JSON.parse(e.data) as Record<string, unknown>
          const dataLen = typeof peek.data === 'string' ? (peek.data as string).length : 0
          vlog('WS_PACKET', `type=${peek.type} data_b64_len=${dataLen}`)
        } catch {}
        _handleSessionMsg(e.data)
      } else {
        const size = e.data instanceof ArrayBuffer ? e.data.byteLength : -1
        vlog('WS_PACKET', `type=BINARY size=${size} — unexpected from server`)
      }
    }

    ws.onerror = () => {
      vlog('VR_SESSION_WS_ERROR', 'WebSocket error — onclose will fire')
    }

    ws.onclose = (e) => {
      console.trace('[WS_CLOSE_CALLSITE]')
      vlog('VR_SESSION_WS_CLOSE', `code=${e.code} reason="${e.reason}" wasClean=${e.wasClean}`)
      vlog('WS_CLOSE_REASON', `code=${e.code} reason="${e.reason}" sessionAlive=${_sessionAlive}`)
      _sessionConnecting = false
      if (!_sessionAlive) return  // stopSession() already ran — do nothing
      vlog('SESSION_WS_DISCONNECT',
        `code=${e.code} reason="${e.reason}" wasClean=${e.wasClean} runtimeId=${_RUNTIME_ID}`)
      vlog('SESSION_DESTROY_REASON',
        `reason=unexpected_ws_close code=${e.code} sessionState=${_snap.sessionState} ` +
        `audioPlaying=${_ttsPlaying} micChunks=${_micChunksSent} micStabilizing=${_micStabilizing}`)
      vlog('SESSION_DESTROY_DIAGNOSTIC',
        `session_active=${_sessionAlive}` +
        ` voice_connected=${ws.readyState === WebSocket.OPEN}` +
        ` mic_active=${!!_micProc}` +
        ` audio_context=${_micCtx?.state ?? 'null'}` +
        ` stream_track_state=${_micCtx ? 'see_MIC_STREAM_TRACK_STATE' : 'no_ctx'}` +
        ` wake_ws=${_wakeWs?.readyState ?? 'null'}` +
        ` session_ws=${ws.readyState}` +
        ` chunks_sent=${_micChunksSent}` +
        ` stabilizing=${_micStabilizing}` +
        ` tts_playing=${_ttsPlaying}` +
        ` session_state=${_snap.sessionState}` +
        ` close_code=${e.code}`)
      console.trace('[SESSION_WS_DISCONNECT_TRACE]')
      _sessionAlive = false
      _stopMic()
      _audioStop()
      _setState('idle')
      _notify({ sessionActive: false })
      vlog('SESSION_DESTROY', `id=${_RUNTIME_ID} reason=unexpected_ws_close code=${e.code}`)
      window.dispatchEvent(new Event('xyron:session-end'))
    }
  } catch (err) {
    vlog('VR_SESSION_START_FATAL', `${err instanceof Error ? err.message : String(err)}`)
    _sessionConnecting = false
    _sessionAlive      = false
    _notify({ sessionState: 'idle', sessionActive: false })
  }
}

export function stopSession(): void {
  if (!_sessionAlive && !_sessionConnecting) return
  console.trace('[SESSION_WS_DISCONNECT_TRACE]')
  vlog('SESSION_DESTROY_REASON', `reason=user_stop alive=${_sessionAlive} connecting=${_sessionConnecting} state=${_snap.sessionState}`)
  _sessionAlive      = false
  _sessionConnecting = false
  _stopMic()
  _audioStop()

  const ws = _sessionWs
  _sessionWs = null
  if (ws && ws.readyState !== WebSocket.CLOSED) {
    vlog('SESSION_WS_DISCONNECT', `code=1000 reason=user_stop runtimeId=${_RUNTIME_ID}`)
    try { ws.close(1000) } catch {}
  }

  _setState('stopped')
  _notify({ sessionActive: false })
  vlog('SESSION_DESTROY', `id=${_RUNTIME_ID} reason=user_stop`)
  window.dispatchEvent(new Event('xyron:session-end'))
  setTimeout(() => {
    if (_snap.sessionState === 'stopped') _setState('idle')
  }, 100)
}

export function clearMessages(): void {
  _notify({ messages: [] })
  _pendingMsgId = null
}

export function dismissFollowUp(): void {
  _notify({ followUp: null })
}

export function isSessionActive(): boolean  { return _sessionAlive }
export function isSessionConnecting(): boolean { return _sessionConnecting }
