'use client'

import { useState, useCallback } from 'react'
import { useApi } from './useApi'
import { api } from '@/lib/api'
import type { Command } from '@/lib/types'

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
