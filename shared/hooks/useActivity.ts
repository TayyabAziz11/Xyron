

import { useApi } from './useApi'
import { api } from '@/lib/api'
import type { ActivityEvent } from '@/lib/types'

export function useActivity(limit = 50, days = 7) {
  return useApi<ActivityEvent[]>(() => api.activity.list(limit, days), {
    interval: 30_000,
  })
}
