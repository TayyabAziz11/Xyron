'use client'

import { useState } from 'react'
import { Check, X, ChevronDown, ChevronUp } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { formatRelativeTime } from '@/lib/utils'
import type { Approval, RiskLevel } from '@/lib/types'

interface ApprovalCardProps {
  approval: Approval
  onApprove?: (id: string) => Promise<void>
  onReject?: (id: string) => Promise<void>
  className?: string
}

const riskVariant: Record<RiskLevel, 'success' | 'warning' | 'error'> = {
  low: 'success',
  medium: 'warning',
  high: 'error',
}

export function ApprovalCard({ approval, onApprove, onReject, className }: ApprovalCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [actioning, setActioning] = useState<'approve' | 'reject' | null>(null)

  const isPending = approval.status === 'pending'

  const handleApprove = async () => {
    if (!onApprove) return
    setActioning('approve')
    try { await onApprove(approval.id) } finally { setActioning(null) }
  }

  const handleReject = async () => {
    if (!onReject) return
    setActioning('reject')
    try { await onReject(approval.id) } finally { setActioning(null) }
  }

  return (
    <div className={`rounded-xl border border-surface-border bg-surface-raised p-5 space-y-4 ${className ?? ''}`}>
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <Badge
              variant={riskVariant[approval.risk_level as RiskLevel] ?? 'warning'}
              size="sm"
            >
              {approval.risk_level} risk
            </Badge>
            {!isPending && (
              <Badge
                variant={approval.status === 'approved' ? 'success' : 'error'}
                size="sm"
              >
                {approval.status}
              </Badge>
            )}
          </div>
          <h3 className="text-sm font-semibold text-text-primary leading-snug">
            {approval.title}
          </h3>
        </div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-text-muted hover:text-text-secondary transition-colors flex-shrink-0"
        >
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>
      </div>

      {/* Description */}
      <p className="text-sm text-text-secondary">{approval.description}</p>

      {/* Expanded details */}
      {expanded && approval.related_plan && (
        <div className="rounded-lg bg-surface-overlay border border-surface-border p-3">
          <p className="text-xs text-text-muted mb-1">Related plan</p>
          <p className="text-xs font-mono text-text-secondary">{approval.related_plan}</p>
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between">
        <p className="text-xs text-text-muted">{formatRelativeTime(approval.created_at)}</p>

        {isPending && (onApprove || onReject) && (
          <div className="flex items-center gap-2">
            <Button
              variant="danger"
              size="sm"
              icon={X}
              loading={actioning === 'reject'}
              onClick={handleReject}
              disabled={!!actioning}
            >
              Reject
            </Button>
            <Button
              size="sm"
              icon={Check}
              loading={actioning === 'approve'}
              onClick={handleApprove}
              disabled={!!actioning}
              className="bg-status-success hover:bg-status-success/80 text-white"
            >
              Approve
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
