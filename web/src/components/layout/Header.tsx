'use client'

import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import { Bell, Settings, Menu } from 'lucide-react'
import { useApprovals } from '@/hooks/useApprovals'

const pageTitles: Record<string, { title: string; subtitle: string }> = {
  '/app/dashboard':     { title: 'DASHBOARD',      subtitle: 'AI Operations Overview' },
  '/app/command-center':{ title: 'COMMAND CENTER', subtitle: 'Direct AI Interface' },
  '/app/approvals':     { title: 'APPROVALS',      subtitle: 'Pending Action Queue' },
  '/app/activity':      { title: 'ACTIVITY',       subtitle: 'Agent Action Audit Trail' },
  '/app/integrations':  { title: 'INTEGRATIONS',   subtitle: 'Connected Services' },
  '/app/workflows':     { title: 'WORKFLOWS',      subtitle: 'Active Agent Tasks' },
  '/app/history':       { title: 'HISTORY',        subtitle: 'Conversation Archive' },
  '/app/stats':         { title: 'ANALYTICS',      subtitle: 'System Intelligence Data' },
  '/app/settings':      { title: 'SETTINGS',       subtitle: 'System Configuration' },
}

export function Header({ onMenuClick }: { onMenuClick?: () => void }) {
  const pathname = usePathname()
  const { data: approvals } = useApprovals('pending')
  const pendingCount = approvals?.length ?? 0
  const [time, setTime] = useState('')

  useEffect(() => {
    const tick = () => setTime(new Date().toLocaleTimeString('en-US', { hour12: false }))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  const page = pageTitles[pathname] ?? { title: 'XYRON', subtitle: '' }

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-[rgba(255,32,32,0.25)] bg-[rgba(10,10,10,0.92)] px-4 backdrop-blur-md lg:px-6">
      {/* Left — page title */}
      <div className="flex items-center gap-3">
        {onMenuClick && (
          <button
            onClick={onMenuClick}
            className="flex h-8 w-8 items-center justify-center rounded text-text-muted hover:text-[#ff2020] transition-colors lg:hidden"
            aria-label="Open navigation"
          >
            <Menu className="h-4 w-4" />
          </button>
        )}
        <div>
          <p className="text-xs font-bold tracking-[0.15em] text-[#ff2020] leading-none">
            {page.title}
          </p>
          {page.subtitle && (
            <p className="mt-0.5 font-mono text-[10px] text-text-muted">{page.subtitle}</p>
          )}
        </div>
      </div>

      {/* Center — system id */}
      <div className="hidden md:flex items-center gap-2">
        <span className="font-mono text-[10px] tracking-widest text-text-muted">
          XYRON OS / v2.0.0 OMEGA
        </span>
        <span className="h-1.5 w-1.5 rounded-full bg-[#00ff88] animate-pulse" />
        <span className="font-mono text-[10px] text-[#00ff88]">OPERATIONAL</span>
      </div>

      {/* Right — clock + icons */}
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs tabular-nums text-[#ff2020]">{time}</span>

        {pendingCount > 0 && (
          <button
            className="relative p-1 text-text-muted hover:text-[#ff2020] transition-colors"
            aria-label={`${pendingCount} pending approvals`}
          >
            <Bell className="h-4 w-4" />
            <span className="absolute -right-0.5 -top-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-[#ff2020] text-[8px] font-bold text-white">
              {pendingCount > 9 ? '9+' : pendingCount}
            </span>
          </button>
        )}

        <button
          className="p-1 text-text-muted hover:text-[#ff2020] transition-colors"
          aria-label="Settings"
        >
          <Settings className="h-4 w-4" />
        </button>
      </div>
    </header>
  )
}
