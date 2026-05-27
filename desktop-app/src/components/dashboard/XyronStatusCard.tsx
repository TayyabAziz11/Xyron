import { Brain, Activity, Shield, Database, Users } from 'lucide-react'
import { safeString, safeNumber } from '@desktop/lib/dashboardSafeData'
import type { BrainStatus } from '@desktop/services/dashboardApi'
import type { CognitionData } from '@desktop/services/dashboardApi'

function safeUpper(value: unknown): string {
  if (typeof value === 'string') return value.toUpperCase()
  if (value === null || value === undefined) return '—'
  return String(value).toUpperCase()
}

interface Row {
  icon: React.ReactNode
  label: string
  value: string | number
  color?: string
}

interface Props {
  brain:     BrainStatus | null
  cognition: CognitionData | null
  online:    boolean
}

export function XyronStatusCard({ brain, cognition, online }: Props) {
  const attention   = safeString(cognition?.attention, '—')
  const mood        = safeString(cognition?.mood_bias, '—')
  const safetyScore = safeNumber(brain?.safety_score, 0)
  const memoryItems = safeNumber(brain?.memory_items, 0)
  const activeAgents= safeNumber(brain?.active_agents, 0)

  const attentionColor = (a: string) => {
    if (a === 'PROCESSING' || a === 'FOCUSED') return '#ff8c00'
    if (a === 'LISTENING')  return '#00e5ff'
    if (a === 'SPEAKING')   return '#a855f7'
    return '#00ff88'
  }

  const rows: Row[] = [
    {
      icon:  <Activity size={10} />,
      label: 'Attention',
      value: attention,
      color: attentionColor(attention),
    },
    {
      icon:  <Brain size={10} />,
      label: 'Mood',
      value: safeUpper(mood),
      color: mood === 'stressed' ? '#ff1f2d' : mood === 'alert' ? '#ff8c00' : '#00ff88',
    },
    {
      icon:  <Shield size={10} />,
      label: 'Safety Score',
      value: brain ? `${safetyScore}%` : '—',
      color: safetyScore < 70 ? '#ff1f2d' : '#00ff88',
    },
    {
      icon:  <Database size={10} />,
      label: 'Memory Items',
      value: brain ? memoryItems : '—',
      color: '#00e5ff',
    },
    {
      icon:  <Users size={10} />,
      label: 'Active Agents',
      value: brain ? activeAgents : '—',
      color: '#a855f7',
    },
  ]

  return (
    <div className="flex flex-col gap-3">
      {/* Header */}
      <div className="flex items-center gap-2 pb-2"
        style={{ borderBottom: '1px solid rgba(255,35,45,0.15)' }}>
        <div className="w-6 h-6 rounded flex items-center justify-center"
          style={{ background: 'rgba(255,31,45,0.15)', border: '1px solid rgba(255,31,45,0.35)' }}>
          <Brain size={12} style={{ color: '#ff1f2d' }} />
        </div>
        <div>
          <div className="font-mono text-[10px] font-semibold text-[#e8ecf0]">Xyron Brain</div>
          <div className="font-mono text-[9px]" style={{ color: online ? '#00ff88' : '#ff1f2d' }}>
            {online ? '● ONLINE' : '● OFFLINE'}
          </div>
        </div>
        {brain?.current_task && (
          <div className="ml-auto font-mono text-[9px] text-[#9ca3af] text-right max-w-[80px] truncate">
            {safeString(brain.current_task)}
          </div>
        )}
      </div>

      {/* Rows */}
      {rows.map(row => (
        <div key={row.label} className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <span style={{ color: row.color ?? '#6b7280' }}>{row.icon}</span>
            <span className="font-mono text-[10px] text-[#9ca3af] tracking-widest uppercase">{row.label}</span>
          </div>
          <span className="font-mono text-[11px] font-semibold" style={{ color: row.color ?? '#e8ecf0' }}>
            {row.value}
          </span>
        </div>
      ))}

      {/* Emotion */}
      {brain && (
        <div className="flex flex-col gap-1 pt-1"
          style={{ borderTop: '1px solid rgba(255,35,45,0.1)' }}>
          <div className="flex items-center justify-between">
            <span className="font-mono text-[10px] text-[#6b7280] tracking-widest uppercase">Emotion</span>
            <span className="font-mono text-[11px] font-semibold" style={{ color: '#ff8c00' }}>
              {safeUpper(brain.emotion_state)}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
