'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Mic, MicOff, FileText, Loader2, Radio } from 'lucide-react'

const API_BASE = typeof window !== 'undefined' ? '' : 'http://localhost:8000'

interface MeetingStatus {
  active:     boolean
  word_count: number
  transcript: string
}

export function MeetingPanel() {
  const [status,    setStatus]    = useState<MeetingStatus | null>(null)
  const [summary,   setSummary]   = useState('')
  const [loading,   setLoading]   = useState(false)
  const [recording, setRecording] = useState(false)
  const mediaRef   = useRef<MediaRecorder | null>(null)
  const pollRef    = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/meeting/status`)
      const d = await r.json()
      setStatus(d)
    } catch { /* ok */ }
  }, [])

  useEffect(() => {
    fetchStatus()
  }, [fetchStatus])

  const startMeeting = async () => {
    await fetch(`${API_BASE}/api/v1/meeting/start`, { method: 'POST' })
    setRecording(true)
    setSummary('')
    fetchStatus()

    // Start sending mic audio in 5s chunks
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream, { mimeType: 'audio/webm' })
      mediaRef.current = mr

      mr.ondataavailable = async (e) => {
        if (e.data.size < 100) return
        const formData = new FormData()
        formData.append('audio', e.data, 'chunk.webm')
        try {
          await fetch(`${API_BASE}/api/v1/meeting/chunk-audio`, { method: 'POST', body: formData })
        } catch { /* ok */ }
      }

      mr.start(5000)   // 5s intervals
    } catch { /* mic permission denied */ }

    pollRef.current = setInterval(fetchStatus, 5000)
  }

  const stopMeeting = async () => {
    mediaRef.current?.stop()
    mediaRef.current?.stream.getTracks().forEach(t => t.stop())
    mediaRef.current = null
    if (pollRef.current) clearInterval(pollRef.current)
    await fetch(`${API_BASE}/api/v1/meeting/stop`, { method: 'POST' })
    setRecording(false)
    await fetchStatus()
  }

  const summarize = async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API_BASE}/api/v1/meeting/summarize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ last_minutes: 30 }),
      })
      const d = await r.json()
      setSummary(d.summary ?? '')
    } catch { /* ok */ } finally { setLoading(false) }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Radio className="h-4 w-4 text-accent-primary" />
        <span className="text-sm font-medium text-text-primary">Meeting Assistant</span>
        {status?.active && (
          <span className="flex items-center gap-1 rounded-full bg-status-error/10 px-2 py-0.5 text-xs text-status-error">
            <span className="h-1.5 w-1.5 rounded-full bg-status-error animate-pulse" />
            Live
          </span>
        )}
      </div>

      {/* Controls */}
      <div className="flex gap-2">
        {!recording ? (
          <button
            onClick={startMeeting}
            className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-accent-primary/10 border border-accent-primary/20 py-3 text-sm font-medium text-accent-primary hover:bg-accent-primary/20 transition-colors"
          >
            <Mic className="h-4 w-4" />
            Start Recording
          </button>
        ) : (
          <button
            onClick={stopMeeting}
            className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-status-error/10 border border-status-error/20 py-3 text-sm font-medium text-status-error hover:bg-status-error/20 transition-colors"
          >
            <MicOff className="h-4 w-4" />
            Stop Recording
          </button>
        )}

        <button
          onClick={summarize}
          disabled={loading || (!status?.word_count && !status?.active)}
          className="flex items-center gap-2 rounded-xl bg-surface-secondary border border-surface-border px-4 py-3 text-sm font-medium text-text-secondary hover:border-accent-primary/30 transition-colors disabled:opacity-40"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
          Summarize
        </button>
      </div>

      {/* Stats */}
      {status && (
        <div className="rounded-xl border border-surface-border bg-surface-secondary/30 p-3">
          <p className="text-xs text-text-muted">
            Status: <span className={status.active ? 'text-status-success' : 'text-text-secondary'}>
              {status.active ? 'Recording' : 'Stopped'}
            </span>
            {status.word_count > 0 && (
              <> · <span className="text-text-secondary">{status.word_count} words captured</span></>
            )}
          </p>
          {status.transcript && (
            <p className="mt-2 text-xs text-text-muted leading-relaxed line-clamp-3">{status.transcript}</p>
          )}
        </div>
      )}

      {/* Summary */}
      {summary && (
        <div className="rounded-xl border border-accent-primary/20 bg-accent-primary/5 p-3">
          <p className="mb-1 text-xs font-medium text-accent-primary">Summary</p>
          <div className="space-y-1">
            {summary.split('\n').filter(Boolean).map((line, i) => (
              <p key={i} className="text-sm text-text-secondary leading-snug">{line}</p>
            ))}
          </div>
        </div>
      )}

      {/* Tips */}
      {!status?.active && !summary && (
        <p className="text-xs text-text-muted text-center">
          Or say <span className="font-mono text-text-secondary">&quot;start meeting&quot;</span> and{' '}
          <span className="font-mono text-text-secondary">&quot;summarize what was said&quot;</span>
        </p>
      )}
    </div>
  )
}
