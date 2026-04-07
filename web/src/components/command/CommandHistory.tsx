import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { Terminal } from 'lucide-react'
import { formatRelativeTime, truncate } from '@/lib/utils'
import type { Command, CommandStatus } from '@/lib/types'

interface CommandHistoryProps {
  commands: Command[]
  className?: string
}

const statusVariant: Record<CommandStatus, 'success' | 'error' | 'pending' | 'info'> = {
  queued: 'info',
  running: 'pending',
  completed: 'success',
  failed: 'error',
}

export function CommandHistory({ commands, className }: CommandHistoryProps) {
  if (commands.length === 0) {
    return (
      <EmptyState
        icon={Terminal}
        title="No commands yet"
        description="Run your first command above"
        className={className}
      />
    )
  }

  return (
    <ul className={className}>
      {commands.map((cmd) => (
        <li
          key={cmd.id}
          className="flex items-start gap-3 py-3 border-b border-surface-border last:border-0"
        >
          <div className="flex-1 min-w-0">
            <p className="text-sm text-text-secondary">{truncate(cmd.text, 80)}</p>
            <p className="text-xs text-text-muted mt-0.5">{formatRelativeTime(cmd.created_at)}</p>
          </div>
          <Badge variant={statusVariant[cmd.status]} size="sm">
            {cmd.status}
          </Badge>
        </li>
      ))}
    </ul>
  )
}
