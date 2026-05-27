import { useMemo } from 'react'
import { Zap } from 'lucide-react'
import { useAuthStore } from '@desktop/stores/authStore'

interface Props {
  online: boolean
}

function getGreeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

export function HeaderGreeting({ online }: Props) {
  const getDisplayName = useAuthStore(s => s.getDisplayName)
  const name = useMemo(() => getDisplayName(), [getDisplayName])
  const greeting = useMemo(getGreeting, [])

  return (
    <div className="flex items-center justify-between">
      <div>
        <p className="font-mono text-[10px] tracking-[0.2em] text-[rgba(255,31,45,0.6)] uppercase mb-0.5">
          XYRON AI SYSTEM
        </p>
        <h1 className="text-2xl font-bold text-[#e8ecf0] leading-tight">
          {greeting},{' '}
          <span className="text-[#ff1f2d]" style={{ textShadow: '0 0 12px rgba(255,31,45,0.6)' }}>
            {name}
          </span>
        </h1>
      </div>

      <div className="flex items-center gap-2 px-3 py-1.5 rounded-full"
        style={{ background: 'rgba(0,255,136,0.08)', border: '1px solid rgba(0,255,136,0.25)' }}>
        <Zap size={12} className="text-[#00ff88]" />
        <span className="font-mono text-[10px] font-semibold tracking-widest"
          style={{ color: online ? '#00ff88' : '#ff1f2d' }}>
          {online ? 'XYRON ONLINE' : 'BACKEND OFFLINE'}
        </span>
        <span className="x-live-dot" style={online ? {} : { background: '#ff1f2d', boxShadow: '0 0 6px rgba(255,31,45,0.8)' }} />
      </div>
    </div>
  )
}
