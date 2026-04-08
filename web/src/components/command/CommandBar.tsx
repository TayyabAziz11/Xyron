'use client'

import { useState, useRef, useCallback } from 'react'
import { ArrowRight, Mic, MicOff, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'

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

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
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
      console.error('Microphone access denied or unavailable:', err)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

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
      }
    } catch (err) {
      console.error('Transcription request failed:', err)
    } finally {
      setIsTranscribing(false)
    }
  }, [onTranscript])

  const toggleRecording = () => {
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
    </form>
  )
}
