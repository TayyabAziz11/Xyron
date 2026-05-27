// Takeover service — wraps /api/v1/takeover/* endpoints

const BASE = '/api/v1/takeover'

// autonomy_level comes from the backend as int (0-3) but may be a string label
// in future — keep the type loose and use formatAutonomyLevel() to display it.
export type AutonomyLevel = string | number | null | undefined

export interface TakeoverStatus {
  active: boolean
  source: string | null
  autonomy_level: AutonomyLevel
  started_at: string | null
  last_action: string | null
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`takeover ${path} → ${r.status}`)
  return r.json()
}

async function post<T>(path: string, body?: object): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`takeover ${path} → ${r.status}`)
  return r.json()
}

export const takeoverApi = {
  status:     () => get<TakeoverStatus>('/status'),
  activate:   () => post<TakeoverStatus>('/activate', {}),
  deactivate: () => post<TakeoverStatus>('/deactivate'),
}
