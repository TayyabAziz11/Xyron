
import { useState, useEffect } from 'react'
import { RefreshCw, Cpu, MemoryStick, HardDrive, Thermometer } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { useSystemMetrics } from '@/hooks/useSystemMetrics'
import { api } from '@/lib/api'

function CyberPanel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`cyber-panel p-4 ${className}`}>{children}</div>
}

function ST({ label, live = false }: { label: string; live?: boolean }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      {live && (
        <span className="relative flex h-2 w-2 flex-shrink-0">
          <span className="absolute inline-flex h-2 w-2 animate-ping rounded-full bg-[#ff2020] opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-[#ff2020]" />
        </span>
      )}
      <span className="font-mono text-[10px] font-semibold tracking-[0.2em] text-[#ff2020]">{label}</span>
      <div className="flex-1 h-px bg-[rgba(255,32,32,0.2)]" />
    </div>
  )
}

function MBar({ label, value, color = '#ff2020' }: { label: string; value: number; color?: string }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between">
        <span className="font-mono text-[11px] text-text-muted">{label}</span>
        <span className="font-mono text-[11px] tabular-nums text-text-secondary">{value}%</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-[rgba(255,255,255,0.06)]">
        <div className="h-full rounded-full transition-all duration-700"
          style={{ width: `${value}%`, background: color, boxShadow: `0 0 6px ${color}` }} />
      </div>
    </div>
  )
}

function Tile({ icon: Icon, label, value, sub, color = '#ff2020' }: {
  icon: React.ElementType; label: string; value: string; sub?: string; color?: string
}) {
  return (
    <CyberPanel className="flex items-center gap-3">
      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded border"
        style={{ borderColor: `${color}40`, background: `${color}10` }}>
        <Icon className="h-5 w-5" style={{ color }} />
      </div>
      <div className="min-w-0">
        <p className="font-mono text-[10px] tracking-widest text-text-muted">{label}</p>
        <p className="font-mono text-lg font-bold leading-tight tabular-nums" style={{ color }}>{value}</p>
        {sub && <p className="font-mono text-[10px] text-text-muted">{sub}</p>}
      </div>
    </CyberPanel>
  )
}

export default function StatsPage() {
  const { data: metrics, error: metricsErr } = useSystemMetrics(2000)
  const { data: commands, refetch } = useApi(() => api.commands.list(200), { interval: 30_000 })

  const [cs, setCs] = useState({ total: 0, completed: 0, failed: 0, rate: 0, topAgent: '' })

  useEffect(() => {
    if (!commands) return
    const total     = commands.length
    const completed = commands.filter(c => c.status === 'completed').length
    const failed    = commands.filter(c => c.status === 'failed').length
    const agents    = commands.map(c => c.agent).filter(Boolean) as string[]
    const freq      = agents.reduce<Record<string, number>>((a, v) => ({ ...a, [v]: (a[v] ?? 0) + 1 }), {})
    const topAgent  = Object.entries(freq).sort((a, b) => b[1] - a[1])[0]?.[0] ?? ''
    setCs({ total, completed, failed, rate: total ? Math.round((completed / total) * 100) : 0, topAgent })
  }, [commands])

  const buckets = Array<number>(7).fill(0)
  if (commands) {
    const now = Date.now()
    commands.forEach(c => {
      const h = Math.floor((now - new Date(c.created_at).getTime()) / 3_600_000)
      if (h >= 0 && h < 7) buckets[6 - h]++
    })
  }
  const maxB = Math.max(...buckets, 1)

  return (
    <div className="space-y-5 p-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-sm font-black tracking-[0.2em] text-[#ff2020]">SYSTEM ANALYTICS</h1>
          <p className="font-mono text-[10px] text-text-muted">Real-time hardware + command intelligence</p>
        </div>
        <button onClick={refetch}
          className="flex items-center gap-2 rounded border border-[rgba(255,32,32,0.3)] px-3 py-1.5 font-mono text-[10px] text-[#ff2020] transition-all hover:bg-[rgba(255,32,32,0.08)]">
          <RefreshCw className="h-3 w-3" /> REFRESH
        </button>
      </div>

      <div>
        <ST label="LIVE SYSTEM METRICS" live />
        {metricsErr ? (
          <p className="font-mono text-[11px] text-[#ef4444]">Backend offline — metrics unavailable</p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Tile icon={Cpu}         label="CPU USAGE"  value={`${metrics?.cpu_pct ?? '—'}%`}
              sub={`${metrics?.cpu_cores ?? '?'} cores`} color="#ff2020" />
            <Tile icon={MemoryStick} label="RAM USAGE"  value={`${metrics?.ram_pct ?? '—'}%`}
              sub={metrics ? `${metrics.ram_used_gb}/${metrics.ram_total_gb} GB` : ''} color="#00ffff" />
            <Tile icon={HardDrive}   label="DISK USAGE" value={`${metrics?.disk_pct ?? '—'}%`}
              sub={metrics ? `${metrics.disk_used_gb}/${metrics.disk_total_gb} GB` : ''} color="#f59e0b" />
            <Tile icon={Thermometer} label="CPU TEMP"
              value={metrics?.temp_c != null ? `${metrics.temp_c}°C` : 'N/A'}
              sub="core temp" color="#00ff88" />
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <CyberPanel>
          <ST label="RESOURCE UTILIZATION" live />
          <div className="space-y-3">
            <MBar label="CPU"  value={metrics?.cpu_pct  ?? 0} color="#ff2020" />
            <MBar label="RAM"  value={metrics?.ram_pct  ?? 0} color="#00ffff" />
            <MBar label="DISK" value={metrics?.disk_pct ?? 0} color="#f59e0b" />
            <MBar label="SWAP" value={metrics?.swap_pct ?? 0} color="#00ff88" />
          </div>
          {metrics && (
            <div className="mt-3 grid grid-cols-2 gap-2 border-t border-[rgba(255,32,32,0.1)] pt-3">
              {[
                { l: 'UPTIME',   v: metrics.uptime_str },
                { l: 'CPU FREQ', v: metrics.cpu_freq_mhz ? `${metrics.cpu_freq_mhz} MHz` : 'N/A' },
                { l: 'NET SENT', v: `${(metrics.net_bytes_sent / 1_048_576).toFixed(1)} MB` },
                { l: 'NET RECV', v: `${(metrics.net_bytes_recv / 1_048_576).toFixed(1)} MB` },
              ].map(({ l, v }) => (
                <div key={l}>
                  <p className="font-mono text-[9px] text-text-muted">{l}</p>
                  <p className="font-mono text-[11px] font-bold tabular-nums text-text-secondary">{v}</p>
                </div>
              ))}
            </div>
          )}
        </CyberPanel>

        <CyberPanel>
          <ST label="COMMAND STATISTICS" />
          <div className="mb-4 grid grid-cols-3 gap-2">
            {[
              { l: 'TOTAL',     v: cs.total,     color: '#ff2020' },
              { l: 'COMPLETED', v: cs.completed, color: '#00ff88' },
              { l: 'FAILED',    v: cs.failed,    color: '#ef4444' },
            ].map(({ l, v, color }) => (
              <div key={l} className="rounded border py-2 text-center"
                style={{ borderColor: `${color}30`, background: `${color}08` }}>
                <p className="font-mono text-xl font-black tabular-nums" style={{ color }}>{v}</p>
                <p className="font-mono text-[9px] text-text-muted">{l}</p>
              </div>
            ))}
          </div>
          <div className="space-y-1.5">
            <div className="flex justify-between">
              <span className="font-mono text-[10px] text-text-muted">SUCCESS RATE</span>
              <span className="font-mono text-[10px] text-[#00ff88]">{cs.rate}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-[rgba(255,255,255,0.06)]">
              <div className="h-full rounded-full bg-[#00ff88] transition-all duration-700"
                style={{ width: `${cs.rate}%`, boxShadow: '0 0 6px rgba(0,255,136,0.5)' }} />
            </div>
            {cs.topAgent && (
              <p className="pt-1 font-mono text-[10px] text-text-muted">
                TOP AGENT: <span className="text-[#00ffff]">{cs.topAgent.toUpperCase()}</span>
              </p>
            )}
          </div>
        </CyberPanel>
      </div>

      <CyberPanel>
        <ST label="COMMAND FREQUENCY — LAST 7 HOURS" />
        <div className="flex h-24 items-end gap-2">
          {buckets.map((n, i) => {
            const h = new Date(); h.setHours(h.getHours() - (6 - i))
            return (
              <div key={i} className="flex flex-1 flex-col items-center gap-1">
                {n > 0 && <span className="font-mono text-[9px] text-[#ff2020] tabular-nums">{n}</span>}
                <div className="w-full rounded-t transition-all duration-700"
                  style={{
                    height: `${Math.max((n / maxB) * 64, n > 0 ? 4 : 2)}px`,
                    background: n > 0 ? 'linear-gradient(to top,#cc0000,#ff4444)' : 'rgba(255,32,32,0.1)',
                    boxShadow: n > 0 ? '0 0 4px rgba(255,32,32,0.4)' : 'none',
                  }} />
                <span className="font-mono text-[8px] text-text-muted">{h.getHours()}H</span>
              </div>
            )
          })}
        </div>
      </CyberPanel>
    </div>
  )
}
