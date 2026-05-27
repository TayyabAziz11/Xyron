import {
  Mail, Linkedin, MessageCircle, Database, Instagram, Zap, Puzzle,
} from 'lucide-react'
import { StatusDot } from '@/components/ui/StatusDot'
import { Badge } from '@/components/ui/Badge'
import { formatRelativeTime } from '@/lib/utils'
import type { Integration, IntegrationStatus } from '@/lib/types'

interface IntegrationCardProps {
  integration: Integration
  className?: string
}

const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  mail: Mail,
  linkedin: Linkedin,
  'message-circle': MessageCircle,
  database: Database,
  instagram: Instagram,
  zap: Zap,
}

const statusVariant: Record<IntegrationStatus, 'success' | 'error' | 'default' | 'warning'> = {
  connected: 'success',
  disconnected: 'default',
  not_configured: 'default',
  error: 'error',
}

const statusLabel: Record<IntegrationStatus, string> = {
  connected: 'Connected',
  disconnected: 'Disconnected',
  not_configured: 'Not configured',
  error: 'Error',
}

function statusToDot(status: IntegrationStatus): 'online' | 'offline' | 'warning' {
  if (status === 'connected') return 'online'
  if (status === 'error') return 'warning'
  return 'offline'
}

export function IntegrationCard({ integration, className }: IntegrationCardProps) {
  const Icon = iconMap[integration.icon ?? ''] ?? Puzzle
  const status = integration.status as IntegrationStatus

  return (
    <div
      className={`rounded-xl border border-surface-border bg-surface-raised p-5 space-y-4 ${className ?? ''}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-surface-overlay border border-surface-border">
            <Icon className="h-5 w-5 text-text-secondary" />
          </div>
          <div>
            <p className="text-sm font-semibold text-text-primary">{integration.name}</p>
            <p className="text-xs text-text-muted">{integration.description}</p>
          </div>
        </div>
        <StatusDot status={statusToDot(status)} />
      </div>

      {/* Status + last sync */}
      <div className="flex items-center justify-between">
        <Badge variant={statusVariant[status]} size="sm">
          {statusLabel[status]}
        </Badge>
        {integration.last_sync && (
          <p className="text-xs text-text-muted">
            {formatRelativeTime(integration.last_sync)}
          </p>
        )}
      </div>

      {/* Credential file hint */}
      {status !== 'connected' && (
        <p className="text-xs text-text-muted">
          {integration.credential_file
            ? `Expects: .secrets/${integration.credential_file}`
            : `Configure credentials to connect`}
        </p>
      )}
    </div>
  )
}
