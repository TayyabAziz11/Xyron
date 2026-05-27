import type {
  ApiResponse,
  Command,
  Approval,
  ActivityEvent,
  Integration,
  Workflow,
  HealthStatus,
  SystemStatus,
  VoiceTranscriptionResult,
  Draft,
  SystemMetrics,
} from './types'

const isTauri = typeof window !== 'undefined' && !!(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
const API_BASE = typeof window === 'undefined'
  ? (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000')
  : isTauri ? 'http://localhost:8000' : ''

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    cache: 'no-store',
  })
  if (!res.ok) {
    throw new ApiError(res.status, `GET ${path} failed: ${res.statusText}`)
  }
  const body = (await res.json()) as ApiResponse<T>
  return body.data
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
  })
  if (!res.ok) {
    throw new ApiError(res.status, `POST ${path} failed: ${res.statusText}`)
  }
  const data = (await res.json()) as ApiResponse<T>
  return data.data
}

async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
  })
  if (!res.ok) {
    throw new ApiError(res.status, `PATCH ${path} failed: ${res.statusText}`)
  }
  const data = (await res.json()) as ApiResponse<T>
  return data.data
}

export const api = {
  health: {
    ping: () => apiGet<HealthStatus>('/api/v1/health'),
    status: () => apiGet<SystemStatus>('/api/v1/status'),
  },

  commands: {
    list: (limit = 20) => apiGet<Command[]>(`/api/v1/commands?limit=${limit}`),
    get: (id: string) => apiGet<Command>(`/api/v1/commands/${id}`),
    submit: (text: string) => apiPost<Command>('/api/v1/commands', { text }),
  },

  approvals: {
    list: (status = 'all') => apiGet<Approval[]>(`/api/v1/approvals?status=${status}`),
    get: (id: string) => apiGet<Approval>(`/api/v1/approvals/${id}`),
    approve: (id: string, note?: string) =>
      apiPost<Approval>(`/api/v1/approvals/${id}/approve`, { note }),
    reject: (id: string, note?: string) =>
      apiPost<Approval>(`/api/v1/approvals/${id}/reject`, { note }),
  },

  activity: {
    list: (limit = 50, days = 7) =>
      apiGet<ActivityEvent[]>(`/api/v1/activity?limit=${limit}&days=${days}`),
    get: (id: string) => apiGet<ActivityEvent>(`/api/v1/activity/${id}`),
  },

  integrations: {
    list: () => apiGet<Integration[]>('/api/v1/integrations'),
    get: (id: string) => apiGet<Integration>(`/api/v1/integrations/${id}`),
  },

  workflows: {
    list: (status = 'all') => apiGet<Workflow[]>(`/api/v1/workflows?status=${status}`),
    get: (id: string) => apiGet<Workflow>(`/api/v1/workflows/${id}`),
  },

  voice: {
    transcribe: (blob: Blob): Promise<VoiceTranscriptionResult> => {
      const form = new FormData()
      form.append('audio', blob, 'recording.webm')
      return fetch(`${API_BASE}/api/v1/voice/transcribe`, {
        method: 'POST',
        body: form,
      })
        .then((r) => r.json())
        .then((body: ApiResponse<VoiceTranscriptionResult>) => body.data)
    },
  },

  system: {
    metrics: () => apiGet<SystemMetrics>('/api/v1/system/metrics'),
  },

  drafts: {
    list:    (status?: string) => apiGet<Draft[]>(`/api/v1/drafts${status ? `?status=${status}` : ''}`),
    pending: () => apiGet<Draft[]>('/api/v1/drafts/pending'),
    get:     (id: string) => apiGet<Draft>(`/api/v1/drafts/${id}`),
    confirm: (id: string) => apiPost<{ draft: Draft; execution: Record<string, unknown>; spoken_response: string }>(`/api/v1/drafts/${id}/confirm`, {}),
    reject:  (id: string) => apiPost<Draft>(`/api/v1/drafts/${id}/reject`, {}),
    edit:    (id: string, content: string) => apiPatch<Draft>(`/api/v1/drafts/${id}`, { content }),
  },
}

export { ApiError }
