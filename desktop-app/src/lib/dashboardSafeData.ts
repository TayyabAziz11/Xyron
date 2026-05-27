/**
 * dashboardSafeData — crash-proof utilities for dashboard components.
 *
 * All values coming from the backend must be treated as `unknown`.
 * Use these helpers before rendering — never call .toFixed / .toUpperCase /
 * .map / .length directly on any backend-derived value.
 */

// ── Warn-once log (dev mode only, no spam) ────────────────────────────────────
const _warned = new Set<string>()

export function warnOnce(source: string, field: string, fallback: unknown) {
  if (process.env.NODE_ENV === 'production') return
  const key = `${source}:${field}`
  if (_warned.has(key)) return
  _warned.add(key)
  console.warn(`[DASHBOARD_DATA_WARN] source=${source} field=${field} fallback=${String(fallback)}`)
}

// ── Scalars ───────────────────────────────────────────────────────────────────

export function safeNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && !isNaN(value) && isFinite(value)) return value
  const n = Number(value)
  return isNaN(n) || !isFinite(n) ? fallback : n
}

export function safeString(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return fallback
  return String(value)
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

/** Clamp to [0, 100]. */
export function safePercent(value: unknown, fallback = 0): number {
  return Math.min(100, Math.max(0, safeNumber(value, fallback)))
}

/**
 * Safe toFixed — the most common crash vector.
 * `metrics.cpu_pct.toFixed(1)` crashes if cpu_pct is undefined/string.
 */
export function safeFixed(value: unknown, digits = 0, fallback = '0'): string {
  const n = safeNumber(value, NaN)
  return Number.isFinite(n) ? n.toFixed(digits) : fallback
}

// ── Arrays ────────────────────────────────────────────────────────────────────

export function safeArray<T>(value: unknown): T[] {
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

// ── Objects ───────────────────────────────────────────────────────────────────

/** Merge backend object with defaults. Handles null / non-object gracefully. */
export function safeObject<T extends Record<string, unknown>>(
  value: unknown,
  fallback: T,
): T {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return { ...fallback, ...(value as Partial<T>) }
  }
  return fallback
}

// ── Field alias resolver ──────────────────────────────────────────────────────

/**
 * Pick the first key from `keys` that exists in obj and is not undefined.
 * Useful when different backend versions use different field names.
 */
export function pickField(obj: Record<string, unknown>, ...keys: string[]): unknown {
  for (const k of keys) {
    if (obj[k] !== undefined) return obj[k]
  }
  return undefined
}
