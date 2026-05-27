import { Mail, Linkedin, Database, MessageCircle, Zap, Activity } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { formatRelativeTime, formatAbsoluteTime } from '@/lib/utils'
import type { ActivityEvent, ActivityStatus } from '@/lib/types'

interface ActivityItemProps {
  event: ActivityEvent
  className?: string
}

function getEventIcon(eventType: string) {
  if (eventType.includes('email') || eventType.includes('gmail')) return Mail
  if (eventType.includes('linkedin')) return Linkedin
  if (eventType.includes('odoo')) return Database
  if (eventType.includes('whatsapp')) return MessageCircle
  if (eventType.includes('openai') || eventType.includes('ai')) return Zap
  return Activity
}

const statusVariant: Record<ActivityStatus, 'success' | 'error' | 'warning' | 'info'> = {
  success: 'success',
  error: 'error',
  warning: 'warning',
  info: 'info',
}

export function ActivityItem({ event, className }: ActivityItemProps) {
  const Icon = getEventIcon(event.event_type)

  return (
    <li className={`flex items-start gap-4 py-3 border-b border-surface-border last:border-0 ${className ?? ''}`}>
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-surface-overlay border border-surface-border">
        <Icon className="h-4 w-4 text-text-muted" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-text-secondary">{event.description}</p>
        {event.actor && (
          <p className="text-xs text-text-muted mt-0.5">via {event.actor}</p>
        )}
        <p className="text-xs text-text-muted mt-0.5" title={formatAbsoluteTime(event.timestamp)}>
          {formatRelativeTime(event.timestamp)}
        </p>
      </div>
      <Badge variant={statusVariant[event.status as ActivityStatus] ?? 'info'} size="sm">
        {event.status}
      </Badge>
    </li>
  )
}
