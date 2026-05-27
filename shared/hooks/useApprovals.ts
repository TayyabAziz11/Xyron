

import { useCallback } from 'react'
import { useApi } from './useApi'
import { api } from '@/lib/api'
import type { Approval } from '@/lib/types'

export function useApprovals(status = 'all') {
  const result = useApi<Approval[]>(() => api.approvals.list(status), {
    interval: 15_000,
  })

  const approve = useCallback(async (id: string, note?: string) => {
    await api.approvals.approve(id, note)
    result.refetch()
  }, [result])

  const reject = useCallback(async (id: string, note?: string) => {
    await api.approvals.reject(id, note)
    result.refetch()
  }, [result])

  return { ...result, approve, reject }
}
