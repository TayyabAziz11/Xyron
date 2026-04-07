'use client'

import { useState } from 'react'
import { Activity } from 'lucide-react'
import { ActivityItem } from '@/components/activity/ActivityItem'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { useActivity } from '@/hooks/useActivity'
import { cn } from '@/lib/utils'

type DayFilter = 1 | 7 | 30
const DAY_FILTERS: { label: string; value: DayFilter }[] = [
  { label: 'Last 24h', value: 1 },
  { label: 'Last 7 days', value: 7 },
  { label: 'Last 30 days', value: 30 },
]

export default function ActivityPage() {
  const [days, setDays] = useState<DayFilter>(7)
  const [limit, setLimit] = useState(50)
  const { data: events, loading, error } = useActivity(limit, days)

  return (
    <div className="max-w-3xl mx-auto space-y-6 animate-fade-in">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex gap-1 rounded-lg bg-surface-raised border border-surface-border p-1">
          {DAY_FILTERS.map(({ label, value }) => (
            <button
              key={value}
              onClick={() => setDays(value)}
              className={cn(
                'rounded-md px-4 py-1.5 text-sm font-medium transition-colors',
                days === value
                  ? 'bg-brand text-white'
                  : 'text-text-muted hover:text-text-secondary',
              )}
            >
              {label}
            </button>
          ))}
        </div>
        {events && (
          <span className="text-xs text-text-muted">{events.length} events</span>
        )}
      </div>

      {loading && (
        <div className="flex justify-center py-16">
          <LoadingSpinner size="lg" />
        </div>
      )}
      {error && (
        <p className="text-sm text-status-error">Failed to load activity: {error.message}</p>
      )}
      {!loading && !error && (!events || events.length === 0) && (
        <EmptyState
          icon={Activity}
          title="No activity recorded yet"
          description="Agent actions and system events will appear here as they happen."
        />
      )}
      {!loading && events && events.length > 0 && (
        <div className="rounded-xl border border-surface-border bg-surface-raised">
          <ul className="px-5">
            {events.map((event) => (
              <ActivityItem key={event.id} event={event} />
            ))}
          </ul>
          {events.length >= limit && (
            <div className="border-t border-surface-border p-4 text-center">
              <button
                onClick={() => setLimit((l) => l + 50)}
                className="text-sm text-brand-light hover:underline"
              >
                Load more
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
