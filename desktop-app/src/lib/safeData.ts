/**
 * safeData — defensive utilities for consuming backend responses.
 *
 * Every backend field should be treated as `unknown` at the boundary.
 * These helpers normalise values so components never receive unexpected types.
 */

// ── Array normalization ───────────────────────────────────────────────────────

/**
 * Coerce any backend value to T[].
 * Handles: array, ApiResponse.data, .commands, .items, .results wrappers.
 */
export function normalizeArray<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>
    if (Array.isArray(obj.data))     return obj.data     as T[]
    if (Array.isArray(obj.commands)) return obj.commands as T[]
    if (Array.isArray(obj.items))    return obj.items    as T[]
    if (Array.isArray(obj.results))  return obj.results  as T[]
  }
  return []
}

// ── Object unwrapping ─────────────────────────────────────────────────────────

/**
 * Unwrap a backend ApiResponse envelope: `{ success, data, message }` → `data`.
 * Falls through safely if the value is already the raw object.
 */
export function unwrapApiResponse<T>(value: unknown): T {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>
    if ('data' in obj && obj.data !== undefined) return obj.data as T
  }
  return value as T
}

/**
 * Merge a backend object with defaults, ignoring non-object responses.
 */
export function normalizeObject<T extends Record<string, unknown>>(
  value: unknown,
  defaults: T,
): T {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return { ...defaults, ...(value as Partial<T>) }
  }
  return defaults
}

// ── Scalar guards ─────────────────────────────────────────────────────────────

export function safeString(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return fallback
  return String(value)
}

export function safeNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && !isNaN(value)) return value
  const n = Number(value)
  return isNaN(n) ? fallback : n
}

export function safeBoolean(value: unknown, fallback = false): boolean {
  if (typeof value === 'boolean') return value
  return fallback
}

export function safeDate(value: unknown): string {
  if (typeof value === 'string' && value) return value
  if (value instanceof Date) return value.toISOString()
  return new Date().toISOString()
}

/** Clamps a number to [0, 100]. */
export function safePercent(value: unknown, fallback = 0): number {
  return Math.min(100, Math.max(0, safeNumber(value, fallback)))
}

// ── Command-item normalizer ───────────────────────────────────────────────────

export interface NormalizedCommand {
  id:          string
  text:        string
  status:      string
  created_at:  string
  agent?:      string
  result?:     string
}

/**
 * Normalise a single raw command record from the backend.
 * Tolerates missing/renamed fields from any response shape.
 */
export function normalizeCommand(raw: unknown): NormalizedCommand {
  const c = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  return {
    id:         safeString(c.id ?? c.command_id, crypto.randomUUID()),
    text:       safeString(c.text ?? c.description ?? c.command, 'Unknown command'),
    status:     safeString(c.status, 'queued'),
    created_at: safeDate(c.created_at ?? c.timestamp),
    agent:      c.agent ? safeString(c.agent) : undefined,
    result:     c.result ? safeString(c.result) : undefined,
  }
}
