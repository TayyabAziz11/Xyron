'use client'

import { useApi } from './useApi'
import { api } from '@/lib/api'
import type { Integration } from '@/lib/types'

export function useIntegrations() {
  return useApi<Integration[]>(() => api.integrations.list(), {
    interval: 60_000,
  })
}
