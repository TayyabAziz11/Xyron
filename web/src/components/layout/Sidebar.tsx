'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  LayoutDashboard,
  Terminal,
  CheckCircle,
  Activity,
  Puzzle,
  GitBranch,
  Settings,
  ArrowLeft,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { StatusDot } from '@/components/ui/StatusDot'
import { useApi } from '@/hooks/useApi'
import { api } from '@/lib/api'
import { useApprovals } from '@/hooks/useApprovals'

const navItems = [
  { href: '/app/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/app/command-center', label: 'Command Center', icon: Terminal },
  { href: '/app/approvals', label: 'Approvals', icon: CheckCircle, showBadge: true },
  { href: '/app/activity', label: 'Activity', icon: Activity },
  { href: '/app/integrations', label: 'Integrations', icon: Puzzle },
  { href: '/app/workflows', label: 'Workflows', icon: GitBranch },
  { href: '/app/settings', label: 'Settings', icon: Settings },
]

export function Sidebar() {
  const pathname = usePathname()
  const { data: health } = useApi(() => api.health.ping(), { interval: 30_000 })
  const { data: approvals } = useApprovals('pending')

  const pendingCount = approvals?.length ?? 0
  const isHealthy = health?.status === 'ok'

  return (
    <aside className="flex h-screen w-60 flex-col border-r border-surface-border bg-surface-raised">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-surface-border">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand">
          <span className="text-sm font-bold text-white">AI</span>
        </div>
        <div>
          <p className="text-sm font-semibold text-text-primary">AI Operator</p>
          <p className="text-xs text-text-muted">Control Panel</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-4 px-2">
        <p className="px-3 mb-2 text-xs font-medium uppercase tracking-wider text-text-muted">
          Navigation
        </p>
        <ul className="space-y-0.5">
          {navItems.map(({ href, label, icon: Icon, showBadge }) => {
            const isActive = pathname === href || pathname.startsWith(href + '/')
            return (
              <li key={href}>
                <Link
                  href={href}
                  className={cn(
                    'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors',
                    isActive
                      ? 'bg-brand/10 text-brand-light border-l-2 border-brand pl-[10px]'
                      : 'text-text-secondary hover:bg-surface-hover hover:text-text-primary border-l-2 border-transparent pl-[10px]',
                  )}
                >
                  <Icon className="h-4 w-4 flex-shrink-0" />
                  <span className="flex-1">{label}</span>
                  {showBadge && pendingCount > 0 && (
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand text-[10px] font-bold text-white">
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
          className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-text-muted hover:text-text-secondary hover:bg-surface-hover transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to home
        </Link>
      </div>

      {/* System status */}
      <div className="border-t border-surface-border p-4">
        <div className="flex items-center gap-2.5 rounded-lg bg-surface-overlay px-3 py-2.5">
          <StatusDot status={isHealthy ? 'online' : 'offline'} />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-text-secondary">
              {isHealthy ? 'API Connected' : 'API Offline'}
            </p>
            <p className="text-xs text-text-muted truncate">localhost:8000</p>
          </div>
        </div>
      </div>
    </aside>
  )
}
