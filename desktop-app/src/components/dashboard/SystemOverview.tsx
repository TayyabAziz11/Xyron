import { memo, useMemo } from 'react'
import { NeuralMesh } from './NeuralMesh'
import { safeFixed, safePercent, safeNumber } from '@desktop/lib/dashboardSafeData'
import type { MetricsData } from '@desktop/services/dashboardApi'

// ── Sparkline (SVG mini chart) ────────────────────────────────────────────────
function Sparkline({ values, color, width = 64, height = 24 }: {
  values: number[]; color: string; width?: number; height?: number
}) {
  const path = useMemo(() => {
    if (!values.length) return ''
    const max = Math.max(...values, 1)
    const pts = values.map((v, i) => {
      const x = (i / (values.length - 1 || 1)) * width
      const y = height - (v / max) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    return `M${pts.join('L')}`
  }, [values, width, height])

  if (!path) return null
  return (
    <svg width={width} height={height} style={{ overflow: 'visible' }}>
      <path d={path} fill="none" stroke={color} strokeWidth={1.2}
        strokeLinecap="round" strokeLinejoin="round" opacity={0.7} />
    </svg>
  )
}

// ── Circular gauge ────────────────────────────────────────────────────────────
function Gauge({ value, label, color = '#ff1f2d', size = 64, sparkValues }: {
  value: number; label: string; color?: string; size?: number; sparkValues?: number[]
}) {
  const r    = size * 0.36
  const circ = 2 * Math.PI * r
  const pct  = Math.min(Math.max(value / 100, 0), 1)
  const sw   = 4

  return (
    <div className="flex flex-col items-center gap-1">
      <div style={{ width: size, height: size, position: 'relative' }}>
        <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
          <circle cx={size/2} cy={size/2} r={r} fill="none"
            stroke="rgba(255,255,255,0.06)" strokeWidth={sw} />
          <circle cx={size/2} cy={size/2} r={r} fill="none"
            stroke={color} strokeWidth={sw} strokeLinecap="round"
            strokeDasharray={`${pct * circ} ${circ}`}
            style={{ transition: 'stroke-dasharray 0.6s ease' }} />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-mono font-bold text-[12px]" style={{ color: '#e8ecf0' }}>
            {value === null || value === undefined ? '—' : Math.round(value)}
          </span>
          <span className="font-mono text-[8px] text-[#9ca3af]">%</span>
        </div>
      </div>
      <span className="font-mono text-[9px] tracking-widest text-[#9ca3af] uppercase">{label}</span>
      {sparkValues && sparkValues.length > 1 && (
        <div style={{ opacity: 0.6 }}>
          <Sparkline values={sparkValues} color={color} width={56} height={16} />
        </div>
      )}
    </div>
  )
}

// ── Horizontal perf bar ───────────────────────────────────────────────────────
function PerfBar({ label, value, color = '#ff1f2d', sub, sparkValues }: {
  label: string; value: number; color?: string; sub?: string; sparkValues?: number[]
}) {
  const pct = Math.min(Math.max(value, 0), 100)
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex justify-between items-center">
        <span className="font-mono text-[10px] text-[#9ca3af] tracking-widest uppercase">{label}</span>
        <div className="flex items-center gap-2">
          {sparkValues && sparkValues.length > 1 && (
            <Sparkline values={sparkValues} color={color} width={40} height={12} />
          )}
          <span className="font-mono text-[11px] font-semibold" style={{ color }}>{Math.round(pct)}%</span>
        </div>
      </div>
      <div className="h-1 rounded-full w-full" style={{ background: 'rgba(255,255,255,0.05)' }}>
        <div className="h-full rounded-full"
          style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${color}99, ${color})`,
                   transition: 'width 0.5s ease' }} />
      </div>
      {sub && <span className="font-mono text-[9px] text-[#6b7280]">{sub}</span>}
    </div>
  )
}

// ── Network row ───────────────────────────────────────────────────────────────
function NetRow({ up, down, upStr, downStr, upHistory, downHistory }: {
  up: number; down: number; upStr?: string | null; downStr?: string | null
  upHistory: number[]; downHistory: number[]
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] text-[#9ca3af] tracking-widest uppercase">Network</span>
        <div className="flex gap-3">
          <Sparkline values={upHistory}   color="#00e5ff" width={36} height={12} />
          <Sparkline values={downHistory} color="#a855f7" width={36} height={12} />
        </div>
      </div>
      <div className="flex gap-3 items-center">
        <span className="font-mono text-[9px]" style={{ color: '#00e5ff' }}>
          ↑ {upStr ?? `${safeFixed(up, 0)} KB/s`}
        </span>
        <span className="font-mono text-[9px]" style={{ color: '#a855f7' }}>
          ↓ {downStr ?? `${safeFixed(down, 0)} KB/s`}
        </span>
      </div>
    </div>
  )
}

// ── Battery pill ──────────────────────────────────────────────────────────────
function BatteryPill({ pct, charging }: { pct: number | null | undefined; charging?: boolean | null }) {
  if (pct == null) return null
  const color = pct > 50 ? '#00ff88' : pct > 20 ? '#ff8c00' : '#ff1f2d'
  return (
    <div className="flex items-center gap-1.5">
      <span className="font-mono text-[9px] text-[#9ca3af] tracking-widest uppercase">Battery</span>
      <div className="flex items-center gap-0.5">
        <div className="relative h-3 w-6 rounded-sm border border-white/20" style={{ background: 'rgba(255,255,255,0.04)' }}>
          <div className="absolute left-0 top-0 h-full rounded-sm"
            style={{ width: `${pct}%`, background: color, transition: 'width 1s ease' }} />
        </div>
        <span className="font-mono text-[9px]" style={{ color }}>
          {Math.round(pct)}%{charging ? ' ⚡' : ''}
        </span>
      </div>
    </div>
  )
}

// ── Health ring ───────────────────────────────────────────────────────────────
function HealthRing({ score }: { score: number }) {
  const size  = 52
  const r     = size * 0.38
  const circ  = 2 * Math.PI * r
  const safe  = Math.min(Math.max(score, 0), 100)
  const pct   = safe / 100
  const color = safe >= 80 ? '#00ff88' : safe >= 50 ? '#ff8c00' : '#ff1f2d'
  return (
    <div className="flex flex-col items-center gap-1">
      <div style={{ width: size, height: size, position: 'relative' }}>
        <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
          <circle cx={size/2} cy={size/2} r={r} fill="none"
            stroke="rgba(255,255,255,0.05)" strokeWidth={3} />
          <circle cx={size/2} cy={size/2} r={r} fill="none"
            stroke={color} strokeWidth={3} strokeLinecap="round"
            strokeDasharray={`${pct * circ} ${circ}`} />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="font-mono text-[10px] font-bold" style={{ color }}>{Math.round(safe)}</span>
        </div>
      </div>
      <span className="font-mono text-[9px] text-[#9ca3af] tracking-widest">HEALTH</span>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
interface Props {
  metrics:      MetricsData | null
  history?:     Record<string, number[]>
  healthScore?: number
}

export const SystemOverview = memo(function SystemOverview({
  metrics, history = {}, healthScore = 0,
}: Props) {
  const cpu  = safePercent(metrics?.cpu_pct)
  const ram  = safePercent(metrics?.ram_pct)
  const disk = safePercent(metrics?.disk_pct)
  const gpu  = metrics?.gpu_pct != null ? safePercent(metrics.gpu_pct) : null
  const rawTemp = metrics?.temp_c
  const temp = rawTemp != null ? safeNumber(rawTemp) : null

  const tempColor  = temp == null ? '#6b7280'
    : temp < 60 ? '#00ff88' : temp < 80 ? '#ff8c00' : '#ff1f2d'

  const cpuSub  = metrics
    ? `${safeFixed(metrics.cpu_pct, 1)}%${metrics.cpu_freq_mhz ? ` · ${(metrics.cpu_freq_mhz / 1000).toFixed(2)} GHz` : ''}`
    : '—'
  const ramSub  = metrics
    ? `${safeFixed(metrics.ram_used_gb, 1)} / ${safeFixed(metrics.ram_total_gb, 1)} GB`
    : '—'
  const diskSub = metrics
    ? `${safeFixed(metrics.disk_used_gb, 0)} / ${safeFixed(metrics.disk_total_gb, 0)} GB`
    : '—'

  const h = (key: string) => history[key] ?? []

  return (
    <div className="flex flex-col gap-3 h-full">
      <div className="flex items-center justify-between">
        <span className="x-section-label">System Monitor</span>
        <div className="flex items-center gap-2">
          <BatteryPill pct={metrics?.battery_pct} charging={metrics?.battery_charging} />
          <HealthRing score={safeNumber(healthScore)} />
        </div>
      </div>

      {/* Gauges row: CPU, RAM, GPU (if available), DISK */}
      <div className="flex justify-around py-1">
        <Gauge value={cpu}  label="CPU"  color="#ff1f2d" sparkValues={h('cpu')} />
        <Gauge value={ram}  label="RAM"  color="#a855f7" sparkValues={h('ram')} />
        {gpu != null
          ? <Gauge value={gpu}  label="GPU"  color="#f59e0b" sparkValues={h('gpu')} />
          : <Gauge value={disk} label="DISK" color="#00e5ff" />
        }
        {temp != null && (
          <Gauge value={Math.min(temp, 100)} label="TEMP" color={tempColor} />
        )}
      </div>

      {/* Neural mesh */}
      <div className="flex-1 relative rounded overflow-hidden min-h-[44px]"
        style={{ border: '1px solid rgba(255,31,45,0.12)' }}>
        <NeuralMesh className="absolute inset-0" />
      </div>

      {/* Perf bars */}
      <div className="flex flex-col gap-2">
        <PerfBar label="CPU Load" value={cpu}  color="#ff1f2d" sub={cpuSub}  sparkValues={h('cpu')} />
        <PerfBar label="Memory"   value={ram}  color="#a855f7" sub={ramSub}  sparkValues={h('ram')} />
        <PerfBar label="Storage"  value={disk} color="#00e5ff" sub={diskSub} />
        {gpu != null && (
          <PerfBar label="GPU"    value={gpu}  color="#f59e0b"
            sub={metrics?.gpu_name ?? undefined}
            sparkValues={h('gpu')} />
        )}
        <NetRow
          up={metrics?.net_up_kbs   ?? 0}
          down={metrics?.net_down_kbs ?? 0}
          upStr={metrics?.net_up_str}
          downStr={metrics?.net_down_str}
          upHistory={h('net_up')}
          downHistory={h('net_down')}
        />
      </div>

      {/* Uptime + process count */}
      {metrics && (
        <div className="flex justify-between font-mono text-[9px] text-[#6b7280]">
          <span>↑ {metrics.uptime_str}</span>
          {metrics.proc_count != null && <span>{metrics.proc_count} procs</span>}
        </div>
      )}
    </div>
  )
})
