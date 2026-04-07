import { Badge } from '@/components/ui/Badge'
import { formatRelativeTime } from '@/lib/utils'
import type { Workflow, WorkflowStatus } from '@/lib/types'

interface WorkflowItemProps {
  workflow: Workflow
  className?: string
}

const statusVariant: Record<WorkflowStatus, 'success' | 'error' | 'pending' | 'info' | 'default'> = {
  active: 'pending',
  completed: 'success',
  failed: 'error',
  queued: 'info',
  paused: 'default',
}

export function WorkflowItem({ workflow, className }: WorkflowItemProps) {
  const status = workflow.status as WorkflowStatus

  return (
    <li
      className={`flex items-start gap-4 py-3 border-b border-surface-border last:border-0 ${className ?? ''}`}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm text-text-secondary truncate">{workflow.name}</p>
        <div className="flex items-center gap-2 mt-1 flex-wrap">
          <span className="text-xs text-text-muted capitalize">{workflow.type}</span>
          {workflow.current_step && (
            <>
              <span className="text-xs text-text-muted">·</span>
              <span className="text-xs text-text-muted">{workflow.current_step}</span>
            </>
          )}
          {workflow.agent && (
            <>
              <span className="text-xs text-text-muted">·</span>
              <span className="text-xs text-text-muted">{workflow.agent}</span>
            </>
          )}
        </div>
        <p className="text-xs text-text-muted mt-0.5">{formatRelativeTime(workflow.started_at)}</p>
      </div>
      <Badge variant={statusVariant[status] ?? 'default'} size="sm">
        {workflow.status}
      </Badge>
    </li>
  )
}
