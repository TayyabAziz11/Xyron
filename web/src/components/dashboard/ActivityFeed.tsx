'use client'

import Link from 'next/link'
import { Activity } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { useActivity } from '@/hooks/useActivity'
import { formatRelativeTime } from '@/lib/utils'
import type { ActivityStatus } from '@/lib/types'

const statusVariant: Record<ActivityStatus, 'success' | 'error' | 'warning' | 'info'> = {
  success: 'success',
  error: 'error',
  warning: 'warning',
  info: 'info',
}

export function ActivityFeed() {
  const { data: events, loading, error } = useActivity(5, 1)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Activity</CardTitle>
        <Link href="/activity" className="text-xs text-brand-light hover:underline">
          View all
        </Link>
      </CardHeader>
      <CardContent className="pt-0">
        {loading && (
          <div className="flex justify-center py-8">
            <LoadingSpinner />
          </div>
        )}
        {error && (
          <p className="text-xs text-status-error py-4">Failed to load activity</p>
        )}
        {!loading && !error && (!events || events.length === 0) && (
          <EmptyState icon={Activity} title="No recent activity" />
        )}
        {!loading && events && events.length > 0 && (
          <ul className="space-y-3">
            {events.map((event) => (
              <li key={event.id} className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-text-secondary truncate">{event.description}</p>
                  <p className="text-xs text-text-muted">{formatRelativeTime(event.timestamp)}</p>
                </div>
                <Badge variant={statusVariant[event.status as ActivityStatus] ?? 'info'} size="sm">
                  {event.status}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
