'use client'

import { useState } from 'react'
import { GitBranch } from 'lucide-react'
import { WorkflowItem } from '@/components/workflows/WorkflowItem'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { PageTransition } from '@/components/layout/PageTransition'
import { useWorkflows } from '@/hooks/useWorkflows'
import { cn } from '@/lib/utils'

type WorkflowFilter = 'all' | 'active' | 'completed' | 'failed'
const FILTERS: { label: string; value: WorkflowFilter }[] = [
  { label: 'All', value: 'all' },
  { label: 'Active', value: 'active' },
  { label: 'Completed', value: 'completed' },
  { label: 'Failed', value: 'failed' },
]

export default function WorkflowsPage() {
  const [filter, setFilter] = useState<WorkflowFilter>('all')
  const { data: workflows, loading, error } = useWorkflows(filter)

  return (
    <PageTransition>
      <div className="max-w-3xl mx-auto space-y-6">
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

        {loading && (
          <div className="flex justify-center py-16">
            <LoadingSpinner size="lg" />
          </div>
        )}
        {error && (
          <p className="text-sm text-status-error">Failed to load workflows: {error.message}</p>
        )}
        {!loading && !error && (!workflows || workflows.length === 0) && (
          <EmptyState
            icon={GitBranch}
            title="No workflows found"
            description="Workflows are created when you run commands in the Command Center."
          />
        )}
        {!loading && workflows && workflows.length > 0 && (
          <div className="rounded-xl border border-surface-border bg-surface-raised">
            <ul className="px-5">
              {workflows.map((workflow) => (
                <WorkflowItem key={workflow.id} workflow={workflow} />
              ))}
            </ul>
          </div>
        )}
      </div>
    </PageTransition>
  )
}
