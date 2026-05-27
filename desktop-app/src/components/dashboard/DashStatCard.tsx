import type { LucideIcon } from 'lucide-react'

interface Props {
  icon: LucideIcon
  label: string
  value: string | number
  sub?: string
  color?: string   // css color for icon + accent
  glow?: boolean
}

export function DashStatCard({ icon: Icon, label, value, sub, color = '#ff1f2d', glow = false }: Props) {
  const glowStyle = glow
    ? { boxShadow: `0 0 14px ${color}55, 0 0 30px ${color}22` }
    : {}

  return (
    <div className="x-card p-3 flex flex-col gap-2 relative overflow-hidden" style={glowStyle}>
      {/* accent line */}
      <div className="absolute top-0 left-0 right-0 h-px" style={{ background: `linear-gradient(90deg, transparent, ${color}80, transparent)` }} />

      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] tracking-[0.18em] uppercase text-[#9ca3af]">{label}</span>
        <div className="p-1 rounded" style={{ background: `${color}18` }}>
          <Icon size={12} style={{ color }} />
        </div>
      </div>

      <div className="font-mono font-bold text-xl leading-none" style={{ color: '#e8ecf0' }}>
        {value}
      </div>

      {sub && (
        <div className="font-mono text-[9px] text-[#6b7280]">{sub}</div>
      )}
    </div>
  )
}
