'use client'

import Link from 'next/link'
import Image from 'next/image'
import { usePathname } from 'next/navigation'
import {
  LayoutDashboard, Terminal, CheckCircle, Activity,
  Puzzle, GitBranch, Settings, ArrowLeft, Clock, BarChart2, X,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useApi } from '@/hooks/useApi'
import { api } from '@/lib/api'
import { useApprovals } from '@/hooks/useApprovals'

const navItems = [
  { href: '/app/dashboard',      label: 'DASHBOARD',       icon: LayoutDashboard },
  { href: '/app/command-center', label: 'COMMAND CENTER',  icon: Terminal },
  { href: '/app/approvals',      label: 'APPROVALS',       icon: CheckCircle, showBadge: true },
  { href: '/app/activity',       label: 'ACTIVITY',        icon: Activity },
  { href: '/app/integrations',   label: 'INTEGRATIONS',    icon: Puzzle },
  { href: '/app/workflows',      label: 'WORKFLOWS',       icon: GitBranch },
  { href: '/app/history',        label: 'HISTORY',         icon: Clock },
  { href: '/app/stats',          label: 'ANALYTICS',       icon: BarChart2 },
  { href: '/app/settings',       label: 'SETTINGS',        icon: Settings },
]

interface SidebarProps {
  open?: boolean
  onClose?: () => void
}

export function Sidebar({ open = false, onClose }: SidebarProps) {
  const pathname = usePathname()
  const { data: health } = useApi(() => api.health.ping(), { interval: 30_000 })
  const { data: approvals } = useApprovals('pending')

  const pendingCount = approvals?.length ?? 0
  const isHealthy = health?.status === 'ok'

  return (
    <aside
      className={cn(
        'fixed inset-y-0 left-0 z-50 flex w-60 flex-col',
        'border-r border-[rgba(255,32,32,0.25)] bg-[#0a0a0a]',
        'transition-transform duration-300 ease-in-out',
        'lg:translate-x-0',
        open ? 'translate-x-0' : '-translate-x-full',
      )}
    >
      {/* Logo */}
      <div className="flex items-center justify-between gap-3 border-b border-[rgba(255,32,32,0.2)] px-4 py-4">
        <div className="flex items-center gap-3">
          <div className="relative h-9 w-9 flex-shrink-0">
            <Image src="/icon-96.png" alt="Xyron" width={36} height={36} className="rounded-md" priority />
            <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-[#00ff88] border-2 border-[#0a0a0a]" />
          </div>
          <div>
            <p className="text-sm font-black tracking-[0.2em] text-[#ff2020]">XYRON</p>
            <p className="font-mono text-[9px] text-text-muted tracking-widest">AI OPERATOR v2.0</p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="flex h-6 w-6 items-center justify-center rounded text-text-muted hover:text-[#ff2020] transition-colors lg:hidden"
          aria-label="Close navigation"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3 px-2">
        <p className="mb-2 px-3 font-mono text-[9px] tracking-[0.25em] text-text-muted">
          NAVIGATION
        </p>
        <ul className="space-y-0.5">
          {navItems.map(({ href, label, icon: Icon, showBadge }) => {
            const isActive = pathname === href || pathname.startsWith(href + '/')
            return (
              <li key={href}>
                <Link
                  href={href}
                  onClick={onClose}
                  className={cn(
                    'flex items-center gap-3 rounded px-3 py-2 text-[11px] font-semibold tracking-wider transition-all duration-150',
                    isActive
                      ? 'bg-[rgba(255,32,32,0.12)] text-[#ff2020] border-l-2 border-[#ff2020] pl-[10px] shadow-[0_0_8px_rgba(255,32,32,0.2)]'
                      : 'border-l-2 border-transparent pl-[10px] text-text-muted hover:bg-[rgba(255,32,32,0.06)] hover:text-text-secondary',
                  )}
                >
                  <Icon className="h-3.5 w-3.5 flex-shrink-0" />
                  <span className="flex-1">{label}</span>
                  {showBadge && pendingCount > 0 && (
                    <span className="flex h-4 w-4 items-center justify-center rounded-full bg-[#ff2020] text-[9px] font-bold text-white">
                      {pendingCount > 9 ? '9+' : pendingCount}
                    </span>
                  )}
                </Link>
              </li>
            )
          })}
        </ul>
      </nav>

      {/* Back to home */}
      <div className="px-2 pb-2">
        <Link
          href="/"
          className="flex items-center gap-2 rounded px-3 py-2 font-mono text-[10px] text-text-muted transition-colors hover:bg-[rgba(255,32,32,0.06)] hover:text-text-secondary"
        >
          <ArrowLeft className="h-3 w-3" />
          BACK TO HOME
        </Link>
      </div>

      {/* System status */}
      <div className="border-t border-[rgba(255,32,32,0.2)] p-3">
        <div className="flex items-center gap-2.5 rounded border border-[rgba(255,32,32,0.15)] bg-[rgba(255,32,32,0.04)] px-3 py-2.5">
          <span
            className={cn(
              'h-2 w-2 flex-shrink-0 rounded-full',
              isHealthy
                ? 'bg-[#00ff88] shadow-[0_0_6px_rgba(0,255,136,0.8)]'
                : 'bg-[#ff2020] animate-pulse',
            )}
          />
          <div className="min-w-0 flex-1">
            <p className="font-mono text-[10px] font-semibold text-text-secondary">
              {isHealthy ? 'API CONNECTED' : 'API OFFLINE'}
            </p>
            <p className="truncate font-mono text-[9px] text-text-muted">localhost:8000</p>
          </div>
        </div>
      </div>
    </aside>
  )
}
