

import { useState, useCallback, useEffect } from 'react'
import { useApi } from './useApi'
import { api } from '@/lib/api'
import type { Command } from '@/lib/types'

// Match the Tauri-aware base from shared/lib/api.ts
const isTauri = typeof window !== 'undefined' && !!(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
const API_BASE = typeof window === 'undefined'
  ? 'http://localhost:8000'
  : isTauri ? 'http://localhost:8000' : ''

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

  // Sync lastResult from polled data — backend processes commands async, so
  // the initial submit response is always 'queued'. We need the completed copy
  // (which has assistant_response) before audio auto-play can fire.
  useEffect(() => {
    if (!lastResult || lastResult.status === 'completed' || lastResult.status === 'failed') return
    const updated = result.data?.find(c => c.id === lastResult.id)
    if (updated && (updated.status === 'completed' || updated.status === 'failed')) {
      setLastResult(updated)
    }
  }, [result.data, lastResult])

  // SSE stream for the in-flight command — fires a refetch the moment the
  // backend marks it completed/failed, so we don't wait up to 10s for the
  // next slow poll to surface the assistant_response.
  //
  // Use result.refetch (stable — useApi wraps it in useCallback with [] deps)
  // not result itself (new object every render) to avoid EventSource churn.
  const { refetch } = result
  const pendingId =
    lastResult && (lastResult.status === 'queued' || lastResult.status === 'running')
      ? lastResult.id
      : null

  const handleCommandUpdate = useCallback((update: Partial<Command>) => {
    if (update.status === 'completed' || update.status === 'failed') {
      refetch()
    }
  }, [refetch])

  useCommandStream(pendingId, handleCommandUpdate)

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
