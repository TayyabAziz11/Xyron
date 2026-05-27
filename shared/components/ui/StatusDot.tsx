import { cn } from '@/lib/utils'

type DotStatus = 'online' | 'offline' | 'warning' | 'pending'

interface StatusDotProps {
  status: DotStatus
  className?: string
}

const statusClasses: Record<DotStatus, string> = {
  online: 'bg-status-success animate-pulse-slow',
  offline: 'bg-text-muted',
  warning: 'bg-status-warning',
  pending: 'bg-status-pending animate-pulse-slow',
}

export function StatusDot({ status, className }: StatusDotProps) {
  return (
    <span
      className={cn('inline-block h-2 w-2 rounded-full', statusClasses[status], className)}
      aria-label={status}
    />
  )
}
