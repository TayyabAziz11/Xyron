

import { useApi } from './useApi'
import { api } from '@/lib/api'
import type { Workflow } from '@/lib/types'

export function useWorkflows(status = 'all') {
  return useApi<Workflow[]>(() => api.workflows.list(status), {
    interval: 15_000,
  })
}
