'use client'

import { useCallback, useState } from 'react'
import { api } from '@/lib/api'
import type { Draft } from '@/lib/types'

export function useDrafts() {
  const [executing, setExecuting] = useState<string | null>(null)

  const getDraft = useCallback(async (id: string): Promise<Draft | null> => {
    try {
      return await api.drafts.get(id)
    } catch {
      return null
    }
  }, [])

  const confirmDraft = useCallback(
    async (id: string): Promise<{ spoken?: string; success: boolean }> => {
      setExecuting(id)
      try {
        const data = await api.drafts.confirm(id)
        return { success: true, spoken: data.spoken_response }
      } catch {
        return { success: false }
      } finally {
        setExecuting(null)
      }
    },
    [],
  )

  const rejectDraft = useCallback(async (id: string): Promise<void> => {
    try {
      await api.drafts.reject(id)
    } catch {
      // non-fatal
    }
  }, [])

  const editDraft = useCallback(async (id: string, content: string): Promise<void> => {
    try {
      await api.drafts.edit(id, content)
    } catch {
      // non-fatal
    }
  }, [])

  return { getDraft, confirmDraft, rejectDraft, editDraft, executing }
}
