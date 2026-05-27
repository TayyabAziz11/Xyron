import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import type { LucideIcon } from 'lucide-react'

interface StatCardProps {
  label: string
  value: number | string | null
  icon: LucideIcon
  href?: string
  color?: 'purple' | 'blue' | 'slate' | 'green'
  loading?: boolean
  className?: string
}

const colorClasses = {
  purple: { icon: 'text-status-pending bg-status-pending/10', value: 'text-text-primary' },
  blue: { icon: 'text-status-info bg-status-info/10', value: 'text-text-primary' },
  slate: { icon: 'text-text-secondary bg-surface-border', value: 'text-text-primary' },
  green: { icon: 'text-status-success bg-status-success/10', value: 'text-text-primary' },
}

export function StatCard({
  label,
  value,
  icon: Icon,
  href,
  color = 'slate',
  loading = false,
  className,
}: StatCardProps) {
  const colors = colorClasses[color]

  const content = (
    <div
      className={cn(
        'bg-surface-raised border border-surface-border rounded-xl p-5 transition-colors',
        href && 'hover:border-brand/30 hover:bg-surface-hover cursor-pointer',
        className,
      )}
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-text-muted mb-2">
            {label}
          </p>
          {loading ? (
            <LoadingSpinner size="sm" />
          ) : (
            <p className={cn('text-2xl font-semibold', colors.value)}>{value ?? '—'}</p>
          )}
        </div>
        <div className={cn('rounded-lg p-2.5', colors.icon)}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </div>
  )

  if (href) return <Link to={href}>{content}</Link>
  return content
}
