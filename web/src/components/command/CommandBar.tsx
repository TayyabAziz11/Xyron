'use client'

import { useState, useRef, useCallback } from 'react'
import { ArrowRight, Mic, MicOff, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'

function micErrorMessage(err: unknown): string {
  if (typeof navigator === 'undefined' || !navigator.mediaDevices) {
    return 'Voice input requires Chrome or Firefox on localhost.'
  }
  if (err instanceof DOMException) {
    if (err.name === 'NotAllowedError')
      return 'Microphone blocked. Click the 🔒 icon in the address bar → allow microphone → try again.'
    if (err.name === 'NotFoundError')
      return 'No microphone found. Connect a mic and try again.'
    if (err.name === 'NotReadableError')
      return 'Microphone is busy. Close other apps using the mic and retry.'
    return `Mic error: ${err.name}`
  }
  return 'Could not access microphone. Check browser permissions.'
}

interface CommandBarProps {
  onSubmit: (text: string) => Promise<void>
  loading?: boolean
  className?: string
  onTranscript?: (text: string) => void
}

export function CommandBar({ onSubmit, loading = false, className, onTranscript }: CommandBarProps) {
  const [text, setText] = useState('')
  const [isRecording, setIsRecording] = useState(false)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [micError, setMicError] = useState<string | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim() || loading) return
    await onSubmit(text.trim())
    setText('')
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleSubmit(e as unknown as React.FormEvent)
    }
  }

  const playGreeting = useCallback((): Promise<void> => {
    return new Promise((resolve) => {
      if (typeof window === 'undefined' || !window.speechSynthesis) {
        resolve()
        return
      }
      window.speechSynthesis.cancel()
      const greetings = [
        "Hi, I'm AI Operator. Go ahead and speak your command.",
        "Hey, I'm listening. What would you like me to do?",
        "AI Operator here. I'm ready — go ahead.",
      ]
      const line = greetings[Math.floor(Math.random() * greetings.length)]
      const utter = new SpeechSynthesisUtterance(line)
      utter.rate   = 1.05
      utter.volume = 0.85
      utter.onend   = () => resolve()
      utter.onerror = () => resolve()
      window.speechSynthesis.speak(utter)
    })
  }, [])

  const startRecording = useCallback(async () => {
    setMicError(null)

    // Guard: mediaDevices only available in secure context (localhost counts)
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setMicError('Voice input is not supported in this browser or context.')
      return
    }

    // Play greeting first, then open mic (prevents greeting being transcribed)
    await playGreeting()

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 },
      })
      chunksRef.current = []

      const recorder = new MediaRecorder(stream)
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = handleRecordingStop
      recorder.start()
      mediaRecorderRef.current = recorder
      setIsRecording(true)
    } catch (err) {
      console.error('[CommandBar] Mic error:', err)
      setMicError(micErrorMessage(err))
    }
  }, [playGreeting]) // eslint-disable-line react-hooks/exhaustive-deps

  const stopRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop()
      recorder.stream.getTracks().forEach((t) => t.stop())
    }
    setIsRecording(false)
  }, [])

  const handleRecordingStop = useCallback(async () => {
    setIsTranscribing(true)
    const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
    const form = new FormData()
    form.append('audio', blob, 'recording.webm')

    try {
      const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
      const resp = await fetch(`${API_BASE}/api/v1/voice/transcribe`, {
        method: 'POST',
        body: form,
      })
      const data = await resp.json()
      const transcript: string = data?.data?.text ?? ''
      if (transcript) {
        setText(transcript)
        onTranscript?.(transcript)
      } else {
        // Speak "didn't catch that" and show a dismissable hint
        if (window.speechSynthesis) {
          const u = new SpeechSynthesisUtterance("Sorry, I didn't catch that. Please try again.")
          u.rate = 1.05
          window.speechSynthesis.speak(u)
        }
        setMicError("Didn't catch that — try speaking clearly after the greeting, then click Stop.")
      }
    } catch (err) {
      console.error('Transcription request failed:', err)
      setMicError('Transcription failed — is the backend running on port 8000?')
    } finally {
      setIsTranscribing(false)
    }
  }, [onTranscript])

  const toggleRecording = () => {
    setMicError(null)
    if (isRecording) {
      stopRecording()
    } else {
      startRecording()
    }
  }

  const micBusy = isRecording || isTranscribing

  return (
    <form onSubmit={handleSubmit} className={className}>
      <div className="relative rounded-xl border border-surface-border bg-surface-raised overflow-hidden focus-within:border-brand/50 focus-within:ring-1 focus-within:ring-brand/20 transition-all">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={4}
          placeholder={
            isRecording
              ? 'Listening… speak now'
              : isTranscribing
              ? 'Transcribing…'
              : 'e.g., Draft a follow-up email to the team about the Monday meeting…'
          }
          className="w-full resize-none bg-transparent px-5 pt-4 pb-14 text-sm text-text-primary placeholder:text-text-muted focus:outline-none"
        />

        <div className="absolute bottom-0 left-0 right-0 flex items-center justify-between border-t border-surface-border bg-surface-overlay/50 px-4 py-2.5">
          <div className="flex items-center gap-3">
            {/* Voice button */}
            <button
              type="button"
              onClick={toggleRecording}
              title={isRecording ? 'Stop recording' : 'Start voice input'}
              className={[
                'flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-all',
                isRecording
                  ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30 animate-pulse'
                  : isTranscribing
                  ? 'bg-brand/10 text-brand cursor-not-allowed opacity-70'
                  : 'text-text-muted hover:text-text-secondary hover:bg-surface-border',
              ].join(' ')}
              disabled={isTranscribing}
            >
              {isTranscribing ? (
                <Loader2 size={14} className="animate-spin" />
              ) : isRecording ? (
                <MicOff size={14} />
              ) : (
                <Mic size={14} />
              )}
              <span>{isRecording ? 'Stop' : isTranscribing ? 'Transcribing' : 'Voice'}</span>
              {isRecording && (
                <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" />
              )}
            </button>

            <p className="text-xs text-text-muted hidden sm:block">
              Press{' '}
              <kbd className="rounded bg-surface-border px-1.5 py-0.5 font-mono text-xs">
                ⌘↵
              </kbd>{' '}
              to run
            </p>
          </div>

          <Button type="submit" size="sm" loading={loading || micBusy} icon={ArrowRight}>
            Run Command
          </Button>
        </div>
      </div>

      {/* Mic error banner — shown below the input when mic fails */}
      {micError && (
        <div className="mt-2 flex items-start gap-2.5 rounded-lg border border-status-error/30 bg-status-error/5 px-3.5 py-2.5 text-xs text-status-error">
          <span className="mt-0.5 shrink-0">⚠</span>
          <span>{micError}</span>
          <button
            type="button"
            onClick={() => setMicError(null)}
            className="ml-auto shrink-0 opacity-60 hover:opacity-100 transition-opacity"
          >
            ✕
          </button>
        </div>
      )}
    </form>
  )
}
