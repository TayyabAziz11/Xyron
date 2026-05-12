'use client'

import type { EnvironmentStatus } from '@/hooks/useEnvironment'

interface Props {
  data: EnvironmentStatus | null
}

function UsageBar({ label, value, red }: { label: string; value: number; red: boolean }) {
  const color = red ? '#ff2020' : '#00ff88'
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] text-text-muted">{label}</span>
        <span className="font-mono text-[10px]" style={{ color }}>{value}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-[rgba(255,255,255,0.06)]">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{
            width: `${value}%`,
            background: color,
            boxShadow: `0 0 6px ${color}`,
          }}
        />
      </div>
    </div>
  )
}

function Skeleton() {
  return (
    <div className="space-y-3 animate-pulse">
      <div className="h-3 w-2/3 rounded bg-[rgba(255,255,255,0.06)]" />
      <div className="h-1.5 w-full rounded-full bg-[rgba(255,255,255,0.06)]" />
      <div className="h-3 w-2/3 rounded bg-[rgba(255,255,255,0.06)]" />
      <div className="h-1.5 w-full rounded-full bg-[rgba(255,255,255,0.06)]" />
      <div className="h-3 w-1/2 rounded bg-[rgba(255,255,255,0.06)]" />
    </div>
  )
}

export default function EnvironmentPanel({ data }: Props) {
  if (!data) return <Skeleton />

  const cpuRed = data.cpu_percent > 80
  const ramRed = data.ram_percent > 80
  const windowLabel =
    data.active_window.length > 40
      ? data.active_window.slice(0, 40) + '…'
      : data.active_window

  return (
    <div className="space-y-3">
      {data.cpu_percent > 90 && (
        <div className="flex items-center gap-2 rounded border border-[rgba(255,32,32,0.5)] bg-[rgba(255,32,32,0.12)] px-3 py-2">
          <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[#ff2020] shadow-[0_0_6px_rgba(255,32,32,0.8)]" />
          <span className="font-mono text-[10px] font-semibold text-[#ff2020]">
            System under heavy load
          </span>
        </div>
      )}

      <UsageBar label="CPU USAGE" value={data.cpu_percent} red={cpuRed} />
      <UsageBar label="RAM USAGE" value={data.ram_percent} red={ramRed} />

      {data.battery_percent !== null && (
        <div className="flex items-center justify-between">
          <span className="font-mono text-[10px] text-text-muted">BATTERY</span>
          <span className="flex items-center gap-1 font-mono text-[10px] text-[#00ff88]">
            {data.battery_charging ? '⚡' : '🔋'}
            {data.battery_percent}%
          </span>
        </div>
      )}

      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-[10px] text-text-muted flex-shrink-0">ACTIVE WINDOW</span>
        <span className="font-mono text-[10px] text-text-secondary text-right truncate max-w-[140px]">
          {windowLabel}
        </span>
      </div>
    </div>
  )
}
