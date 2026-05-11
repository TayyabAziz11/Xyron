'use client'

import { useState, useCallback, useEffect } from 'react'
import { useApi } from './useApi'
import { api } from '@/lib/api'
import type { Command } from '@/lib/types'

const API_BASE = typeof window !== 'undefined' ? '' : 'http://localhost:8000'

export function useCommands() {
  const result = useApi<Command[]>(() => api.commands.list(20), {
    interval: 10_000,
  })
  const [submitting, setSubmitting] = useState(false)
  const [lastResult, setLastResult] = useState<Command | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const submit = useCallback(async (text: string) => {
    if (!text.trim()) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const cmd = await api.commands.submit(text.trim())
      setLastResult(cmd)
      result.refetch()
      return cmd
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Command failed'
      setSubmitError(msg)
      return null
    } finally {
      setSubmitting(false)
    }
  }, [result])

  return { ...result, submit, submitting, lastResult, submitError }
}

/**
 * SSE hook — streams live status updates for a single command.
 *
 * Usage:
 *   useCommandStream(commandId, (cmd) => setCommand(cmd))
 *
 * Events handled: status, done, error, timeout
 */
export function useCommandStream(
  commandId: string | null,
  onUpdate: (cmd: Partial<Command>) => void,
): void {
  useEffect(() => {
    if (!commandId) return

    const es = new EventSource(`${API_BASE}/api/v1/events/commands/${commandId}`)

    const handleStatus = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as Partial<Command>
        onUpdate(data)
      } catch {
        // ignore malformed events
      }
    }

    const handleDone = () => {
      es.close()
    }

    const handleError = () => {
      es.close()
    }

    es.addEventListener('status', handleStatus)
    es.addEventListener('done', handleDone)
    es.addEventListener('error', handleError)
    es.addEventListener('timeout', handleDone)

    return () => {
      es.removeEventListener('status', handleStatus)
      es.removeEventListener('done', handleDone)
      es.removeEventListener('error', handleError)
      es.removeEventListener('timeout', handleDone)
      es.close()
    }
  }, [commandId, onUpdate])
}
