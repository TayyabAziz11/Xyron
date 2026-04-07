'use client'

import { usePathname } from 'next/navigation'
import { Bell } from 'lucide-react'
import { useApprovals } from '@/hooks/useApprovals'

const pageTitles: Record<string, { title: string; subtitle: string }> = {
  '/dashboard': { title: 'Dashboard', subtitle: 'Your AI operations at a glance' },
  '/command': { title: 'Command Center', subtitle: 'Tell AI Operator what to do' },
  '/approvals': { title: 'Approvals', subtitle: 'Review and act on pending requests' },
  '/activity': { title: 'Activity', subtitle: 'Audit trail of all agent actions' },
  '/integrations': { title: 'Integrations', subtitle: 'Connected services and their status' },
  '/workflows': { title: 'Workflows', subtitle: 'Active and completed agent tasks' },
  '/settings': { title: 'Settings', subtitle: 'System configuration and status' },
}

export function Header() {
  const pathname = usePathname()
  const { data: approvals } = useApprovals('pending')
  const pendingCount = approvals?.length ?? 0

  const page = pageTitles[pathname] ?? { title: 'AI Operator', subtitle: '' }

  return (
    <header className="flex h-14 items-center justify-between border-b border-surface-border bg-surface-raised px-6">
      <div>
        <h1 className="text-base font-semibold text-text-primary">{page.title}</h1>
        {page.subtitle && (
          <p className="text-xs text-text-muted">{page.subtitle}</p>
        )}
      </div>

      <div className="flex items-center gap-2">
        {pendingCount > 0 && (
          <div className="relative">
            <Bell className="h-4 w-4 text-text-muted" />
            <span className="absolute -top-1 -right-1 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-brand text-[9px] font-bold text-white">
              {pendingCount > 9 ? '9+' : pendingCount}
            </span>
          </div>
        )}
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-brand/20 text-xs font-semibold text-brand-light">
          OP
        </div>
      </div>
    </header>
  )
}
