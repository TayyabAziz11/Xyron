/**
 * dashboardApi — all backend calls for the dashboard.
 *
 * Every method returns a fully-normalized, type-safe value.
 * No component should ever receive a raw / unknown backend response.
 */

import {
  safeNumber, safeString, safeBoolean, safePercent, safeArray, safeFixed,
  pickField, warnOnce,
} from '@desktop/lib/dashboardSafeData'
import {
  DEFAULT_SYSTEM_METRICS, DEFAULT_BRAIN_STATUS,
  DEFAULT_COGNITION_DATA,
} from '@desktop/lib/dashboardDefaults'

const BASE = '/api/v1'

async function fetchJson(path: string): Promise<unknown> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${path} → ${res.status}`)
  return res.json()
}

/** Unwrap ApiResponse envelope: { success, data } → data */
function unwrap(raw: unknown): unknown {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const obj = raw as Record<string, unknown>
    if (obj.data !== undefined) return obj.data
  }
  return raw
}

// ── Exported types ────────────────────────────────────────────────────────────

export interface HealthStatus {
  status: string
  version?: string
  uptime?: number
}

export interface MetricsData {
  cpu_pct:        number
  cpu_freq_mhz:   number | null
  cpu_name?:      string | null
  cpu_physical_cores?: number
  cpu_logical_cores?:  number
  ram_pct:        number
  ram_used_gb:    number
  ram_free_gb?:   number
  ram_total_gb:   number
  swap_pct?:      number
  disk_pct:       number
  disk_used_gb:   number
  disk_total_gb:  number
  disk_free_gb?:  number
  gpu_pct:        number | null
  gpu_name?:      string | null
  vram_total_mb?: number | null
  vram_used_mb?:  number | null
  temp_c:         number | null
  net_up_kbs:     number
  net_down_kbs:   number
  net_up_str?:    string
  net_down_str?:  string
  battery_pct?:   number | null
  battery_charging?: boolean | null
  proc_count?:    number
  uptime_str:     string
  uptime_s:       number
  // legacy fields kept for backwards compat
  net_bytes_sent?: number
  net_bytes_recv?: number
  // sparkline history (sent on initial WS connect)
  history?: Record<string, number[]>
  // timestamp (ms)
  ts?: number
}

export interface BrainStatus {
  active:         boolean
  autonomy_level: string | number | null
  current_task:   string | null
  emotion_state:  string
  memory_items:   number
  active_agents:  number
  safety_score:   number
  last_updated:   string
}

export interface CognitionData {
  attention:       string
  mood_bias:       string
  active_goal:     string | null
  current_task:    string | null
  context_summary: string
  turn_count:      number
}

export interface CommandItem {
  id:         string
  text:       string
  status:     string
  created_at: string
  result?:    string
  agent?:     string
}

export interface ApprovalItem {
  id:          string
  title:       string
  description: string
  risk_level:  string
  status:      string
  created_at:  string
}

export interface ActivityItem {
  id:          string
  type:        string
  description: string
  timestamp:   string
  success?:    boolean
}

// ── Normalizers ───────────────────────────────────────────────────────────────

export function normalizeSystemMetrics(raw: unknown): MetricsData {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    warnOnce('system/metrics', 'root', '(using defaults)')
    return { ...DEFAULT_SYSTEM_METRICS }
  }
  const o = raw as Record<string, unknown>

  // cpu_pct — backend may use cpu / cpu_usage / cpu_percent / cpu_pct
  const cpuRaw = pickField(o, 'cpu_pct', 'cpu', 'cpu_usage', 'cpu_percent')
  if (cpuRaw === undefined) warnOnce('system/metrics', 'cpu_pct', 0)

  // ram_pct — backend may use ram_pct / memory_pct / ram_usage / mem_percent
  const ramRaw = pickField(o, 'ram_pct', 'memory_pct', 'ram_usage', 'mem_percent')
  if (ramRaw === undefined) warnOnce('system/metrics', 'ram_pct', 0)

  // disk_pct — backend may use disk_pct / disk_usage / storage_pct
  const diskRaw = pickField(o, 'disk_pct', 'disk_usage', 'storage_pct')
  if (diskRaw === undefined) warnOnce('system/metrics', 'disk_pct', 0)

  // temp_c — backend may use temp_c / temperature / cpu_temp
  const tempRaw = pickField(o, 'temp_c', 'temperature', 'cpu_temp')

  return {
    cpu_pct:       safePercent(cpuRaw),
    ram_pct:       safePercent(ramRaw),
    ram_used_gb:   safeNumber(pickField(o, 'ram_used_gb', 'mem_used_gb'), 0),
    ram_total_gb:  safeNumber(pickField(o, 'ram_total_gb', 'mem_total_gb'), 0),
    disk_pct:      safePercent(diskRaw),
    disk_used_gb:  safeNumber(pickField(o, 'disk_used_gb', 'storage_used_gb'), 0),
    disk_total_gb: safeNumber(pickField(o, 'disk_total_gb', 'storage_total_gb'), 0),
    temp_c:        tempRaw !== undefined ? safeNumber(tempRaw) : null,
    uptime_str:    safeString(pickField(o, 'uptime_str', 'uptime'), '—'),
    uptime_s:      safeNumber(pickField(o, 'uptime_s', 'uptime_seconds'), 0),
    net_bytes_sent: safeNumber(o.net_bytes_sent, 0),
    net_bytes_recv: safeNumber(o.net_bytes_recv, 0),
  }
}

export function normalizeBrainStatus(raw: unknown): BrainStatus {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ...DEFAULT_BRAIN_STATUS }
  }
  const o = raw as Record<string, unknown>
  return {
    active:         safeBoolean(o.active ?? o.ok),
    autonomy_level: (o.autonomy_level !== undefined ? o.autonomy_level : 'unknown') as string | number | null,
    current_task:   o.current_task ? safeString(o.current_task) : null,
    emotion_state:  safeString(o.emotion_state ?? o.mood, 'calm'),
    memory_items:   safeNumber(o.memory_items ?? o.memory_count, 0),
    active_agents:  safeNumber(o.active_agents ?? o.agents_count, 0),
    safety_score:   safePercent(o.safety_score ?? o.confidence, 0),
    last_updated:   safeString(o.last_updated, ''),
  }
}

export function normalizeCognitionData(raw: unknown): CognitionData {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ...DEFAULT_COGNITION_DATA }
  }
  const o = raw as Record<string, unknown>
  return {
    attention:       safeString(o.attention, 'IDLE'),
    mood_bias:       safeString(o.mood_bias ?? o.mood_state ?? o.mood, 'neutral'),
    active_goal:     o.active_goal ? safeString(o.active_goal) : null,
    current_task:    o.current_task ? safeString(o.current_task) : null,
    context_summary: safeString(o.context_summary ?? o.summary, ''),
    turn_count:      safeNumber(o.turn_count ?? o.turns, 0),
  }
}

export function normalizeCommands(raw: unknown): CommandItem[] {
  return safeArray<Record<string, unknown>>(raw).map(c => ({
    id:         safeString(c.id ?? c.command_id, crypto.randomUUID()),
    text:       safeString(c.text ?? c.description ?? c.command, 'Unknown command'),
    status:     safeString(c.status, 'queued'),
    created_at: c.created_at ? safeString(c.created_at) : new Date().toISOString(),
    agent:      c.agent ? safeString(c.agent) : undefined,
    result:     c.result ? safeString(c.result) : undefined,
  }))
}

export function normalizeApprovals(raw: unknown): ApprovalItem[] {
  return safeArray<Record<string, unknown>>(raw).map(a => ({
    id:          safeString(a.id, crypto.randomUUID()),
    title:       safeString(a.title ?? a.name, 'Approval Required'),
    description: safeString(a.description ?? a.detail, ''),
    risk_level:  safeString(a.risk_level ?? a.risk, 'medium'),
    status:      safeString(a.status, 'pending'),
    created_at:  a.created_at ? safeString(a.created_at) : new Date().toISOString(),
  }))
}

// ── Public API ────────────────────────────────────────────────────────────────

export const dashboardApi = {
  health: async (): Promise<HealthStatus> => {
    const raw = unwrap(await fetchJson('/health'))
    const o = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
    return { status: safeString(o.status ?? o.health, 'ok'), version: o.version ? safeString(o.version) : undefined }
  },

  brain: async (): Promise<BrainStatus> => {
    const raw = unwrap(await fetchJson('/brain/status'))
    return normalizeBrainStatus(raw)
  },

  metrics: async (): Promise<MetricsData> => {
    const raw = unwrap(await fetchJson('/system/metrics'))
    return normalizeSystemMetrics(raw)
  },

  cognition: async (): Promise<CognitionData> => {
    const raw = unwrap(await fetchJson('/cognition/state'))
    return normalizeCognitionData(raw)
  },

  commands: async (): Promise<CommandItem[]> => {
    const raw = await fetchJson('/commands?limit=20')
    return normalizeCommands(raw)
  },

  approvals: async (): Promise<ApprovalItem[]> => {
    const raw = await fetchJson('/approvals?status=pending')
    return normalizeApprovals(raw)
  },

  activity: async (): Promise<ActivityItem[]> => {
    const raw = await fetchJson('/activity?limit=30')
    return safeArray<ActivityItem>(raw)
  },
}

// Re-export safeFixed so components can use it without another import
export { safeFixed }
