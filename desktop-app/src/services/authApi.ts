// Thin API layer for auth-related backend calls.
// Sends X-User-Id header on every request so the backend can resolve the user.

const BASE = 'http://localhost:8000/api/v1'

function headers(userId?: string): HeadersInit {
  const h: HeadersInit = { 'Content-Type': 'application/json' }
  if (userId) (h as Record<string, string>)['X-User-Id'] = userId
  return h
}

export interface BackendUser {
  id: string
  email: string
  name: string
  preferred_name: string | null
  tier: string
  image_url: string | null
  created_at: number
  last_seen: number
}

export async function syncUser(user: {
  id: string; email: string; name: string; image_url?: string | null
}): Promise<BackendUser | null> {
  try {
    const r = await fetch(`${BASE}/auth/sync`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(user),
      signal: AbortSignal.timeout(5000),
    })
    return r.ok ? r.json() : null
  } catch {
    return null
  }
}

export async function getMe(userId: string): Promise<BackendUser | null> {
  try {
    const r = await fetch(`${BASE}/auth/me`, {
      headers: headers(userId),
      signal: AbortSignal.timeout(5000),
    })
    return r.ok ? r.json() : null
  } catch {
    return null
  }
}

export async function updateTier(userId: string, tier: string): Promise<BackendUser | null> {
  try {
    const r = await fetch(`${BASE}/auth/tier`, {
      method: 'POST',
      headers: headers(userId),
      body: JSON.stringify({ tier }),
      signal: AbortSignal.timeout(5000),
    })
    return r.ok ? r.json() : null
  } catch {
    return null
  }
}

export async function setPreferredName(userId: string, preferredName: string): Promise<BackendUser | null> {
  try {
    const r = await fetch(`${BASE}/auth/preferred-name`, {
      method: 'POST',
      headers: headers(userId),
      body: JSON.stringify({ preferred_name: preferredName }),
      signal: AbortSignal.timeout(5000),
    })
    return r.ok ? r.json() : null
  } catch {
    return null
  }
}
