import { CheckCircle, XCircle, Clock, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Command } from '@/lib/types'

interface CommandResultProps {
  command: Command
  className?: string
}

const statusConfig = {
  queued: { icon: Clock, label: 'Queued', color: 'text-text-muted' },
  running: { icon: Loader2, label: 'Running', color: 'text-status-info' },
  completed: { icon: CheckCircle, label: 'Completed', color: 'text-status-success' },
  failed: { icon: XCircle, label: 'Failed', color: 'text-status-error' },
}

export function CommandResult({ command, className }: CommandResultProps) {
  const config = statusConfig[command.status] ?? statusConfig.queued
  const Icon = config.icon

  return (
    <div
      className={cn(
        'rounded-xl border border-surface-border bg-surface-raised p-5 space-y-3',
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <Icon className={cn('h-4 w-4', config.color, command.status === 'running' && 'animate-spin')} />
        <span className={cn('text-sm font-medium', config.color)}>{config.label}</span>
      </div>

      <div>
        <p className="text-xs text-text-muted mb-1">Command</p>
        <p className="text-sm text-text-secondary">{command.text}</p>
      </div>

      {command.result && (
        <div>
          <p className="text-xs text-text-muted mb-1">Result</p>
          <div className="rounded-lg bg-surface-overlay border border-surface-border p-3">
            <pre className="text-xs text-text-secondary whitespace-pre-wrap font-sans">
              {command.result}
            </pre>
          </div>
        </div>
      )}

      {command.error && (
        <div className="rounded-lg bg-status-error/5 border border-status-error/20 p-3">
          <p className="text-xs text-status-error">{command.error}</p>
        </div>
      )}
    </div>
  )
}
