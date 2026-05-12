'use client'

/**
 * Singleton audio manager for the I'm Home protocol.
 * Uses Web Audio API + GainNode so volume can be boosted above the
 * hardware 1.0 cap that HTMLAudioElement enforces.
 * Guarantees exactly one active stream at all times.
 */

class ImHomeAudioManager {
  private source:   AudioBufferSourceNode | null = null
  private audioCtx: AudioContext | null           = null
  private objectUrl: string | null                = null
  private resolveFn: (() => void) | null          = null

  private getCtx(): AudioContext {
    if (!this.audioCtx || this.audioCtx.state === 'closed') {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const Cls = window.AudioContext ?? (window as any).webkitAudioContext
      this.audioCtx = new Cls() as AudioContext
    }
    return this.audioCtx
  }

  cancel() {
    try {
      this.source?.stop()
      this.source?.disconnect()
    } catch { /* already stopped */ }
    this.source = null

    if (this.objectUrl) {
      try { URL.revokeObjectURL(this.objectUrl) } catch { /* ok */ }
      this.objectUrl = null
    }

    this.resolveFn?.()
    this.resolveFn = null

    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event('xyron:tts-end'))
    }
  }

  async playBlob(blob: Blob): Promise<void> {
    this.cancel()

    if (typeof window === 'undefined') return

    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event('xyron:tts-start'))
    }

    return new Promise<void>((resolve) => {
      this.resolveFn = resolve

      const url = URL.createObjectURL(blob)
      this.objectUrl = url

      // Decode via Web Audio API so we can apply gain amplification
      fetch(url)
        .then(r => r.arrayBuffer())
        .then(buf => {
          const ctx = this.getCtx()

          // Resume if suspended (browser autoplay policy)
          if (ctx.state === 'suspended') ctx.resume()

          ctx.decodeAudioData(buf, (decoded) => {
            const src  = ctx.createBufferSource()
            const gain = ctx.createGain()

            // 2.0 = twice the hardware volume — clearly audible in any environment
            gain.gain.setValueAtTime(2.0, ctx.currentTime)

            src.buffer = decoded
            src.connect(gain)
            gain.connect(ctx.destination)

            this.source = src

            src.onended = () => {
              try { URL.revokeObjectURL(url) } catch { /* ok */ }
              this.objectUrl = null
              this.source    = null
              this.resolveFn = null
              if (typeof window !== 'undefined') {
                window.dispatchEvent(new Event('xyron:tts-end'))
              }
              resolve()
            }

            src.start(0)
          }, () => {
            // decodeAudioData failed — fall back to HTMLAudioElement
            try { URL.revokeObjectURL(url) } catch { /* ok */ }
            const audio    = new Audio(url)
            audio.volume   = 1.0
            audio.onended  = () => { this.resolveFn = null; resolve() }
            audio.onerror  = () => { this.resolveFn = null; resolve() }
            audio.play().catch(() => { this.resolveFn = null; resolve() })
          })
        })
        .catch(() => { this.resolveFn = null; resolve() })
    })
  }

  get isPlaying(): boolean {
    return this.source !== null
  }
}

export const imHomeAudio = new ImHomeAudioManager()
