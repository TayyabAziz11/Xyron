/**
 * dashboardDefaults — safe zero-state shapes for all dashboard data.
 *
 * When backend is offline or returns malformed data, components receive
 * these defaults rather than undefined/null. All values are display-safe.
 */

export const DEFAULT_SYSTEM_METRICS = {
  cpu_pct:       0,
  ram_pct:       0,
  ram_used_gb:   0,
  ram_total_gb:  0,
  disk_pct:      0,
  disk_used_gb:  0,
  disk_total_gb: 0,
  temp_c:        null as number | null,
  uptime_str:    '—',
  uptime_s:      0,
  net_bytes_sent: 0,
  net_bytes_recv: 0,
} as const

export const DEFAULT_BRAIN_STATUS = {
  active:            false,
  autonomy_level:    'unknown' as string | number | null,
  current_task:      null as string | null,
  emotion_state:     'calm',
  memory_items:      0,
  active_agents:     0,
  safety_score:      0,
  last_updated:      '',
} as const

export const DEFAULT_COGNITION_DATA = {
  attention:       'IDLE',
  mood_bias:       'neutral',
  active_goal:     null as string | null,
  current_task:    null as string | null,
  context_summary: '',
  turn_count:      0,
} as const

export const DEFAULT_TAKEOVER_STATUS = {
  active:          false,
  mode:            'standby',
  autonomy_level:  'unknown' as string | number | null,
  source:          'system',
  started_at:      null as string | null,
  last_action:     null as string | null,
} as const

export type DefaultMetrics   = typeof DEFAULT_SYSTEM_METRICS
export type DefaultBrain     = typeof DEFAULT_BRAIN_STATUS
export type DefaultCognition = typeof DEFAULT_COGNITION_DATA
export type DefaultTakeover  = typeof DEFAULT_TAKEOVER_STATUS
