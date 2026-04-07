'use client'

import { useState } from 'react'
import { CheckCircle } from 'lucide-react'
import { ApprovalCard } from '@/components/approvals/ApprovalCard'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { useApprovals } from '@/hooks/useApprovals'
import { cn } from '@/lib/utils'

type FilterStatus = 'all' | 'pending' | 'approved' | 'rejected'
const FILTERS: { label: string; value: FilterStatus }[] = [
  { label: 'All', value: 'all' },
  { label: 'Pending', value: 'pending' },
  { label: 'Approved', value: 'approved' },
  { label: 'Rejected', value: 'rejected' },
]

export default function ApprovalsPage() {
  const [filter, setFilter] = useState<FilterStatus>('all')
  const { data: approvals, loading, error, approve, reject } = useApprovals(filter)

  const pendingCount = approvals?.filter((a) => a.status === 'pending').length ?? 0

  return (
    <div className="max-w-3xl mx-auto space-y-6 animate-fade-in">
      {/* Page heading with count */}
      <div className="flex items-center gap-3">
        <h2 className="text-2xl font-semibold text-text-primary">Approvals</h2>
        {pendingCount > 0 && (
          <span className="flex h-6 min-w-[24px] items-center justify-center rounded-full bg-brand px-2 text-xs font-bold text-white">
            {pendingCount}
          </span>
        )}
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 rounded-lg bg-surface-raised border border-surface-border p-1 w-fit">
        {FILTERS.map(({ label, value }) => (
          <button
            key={value}
            onClick={() => setFilter(value)}
            className={cn(
              'rounded-md px-4 py-1.5 text-sm font-medium transition-colors',
              filter === value
                ? 'bg-brand text-white'
                : 'text-text-muted hover:text-text-secondary',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      {loading && (
        <div className="flex justify-center py-16">
          <LoadingSpinner size="lg" />
        </div>
      )}
      {error && (
        <p className="text-sm text-status-error">Failed to load approvals: {error.message}</p>
      )}
      {!loading && !error && (!approvals || approvals.length === 0) && (
        <EmptyState
          icon={CheckCircle}
          title="No pending approvals"
          description="All actions have been reviewed. New requests will appear here."
        />
      )}
      {!loading && approvals && approvals.length > 0 && (
        <div className="space-y-4">
          {approvals.map((approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              onApprove={approval.status === 'pending' ? approve : undefined}
              onReject={approval.status === 'pending' ? reject : undefined}
            />
          ))}
        </div>
      )}
    </div>
  )
}
