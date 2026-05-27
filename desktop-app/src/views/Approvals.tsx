
import { useState } from 'react'
import { useApprovals } from '@/hooks/useApprovals'
import { api } from '@/lib/api'
import type { Approval } from '@/lib/types'
import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/lib/utils'

// ── Shared UI ──────────────────────────────────────────────────────────────

function CyberPanel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`cyber-panel p-4 ${className}`}>{children}</div>
}

function SectionTitle({ label, live = false }: { label: string; live?: boolean }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      {live && (
        <span className="relative flex h-2 w-2 flex-shrink-0">
          <span className="absolute inline-flex h-2 w-2 animate-ping rounded-full bg-[#ff2020] opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-[#ff2020]" />
        </span>
      )}
      <span className="font-mono text-[10px] font-semibold tracking-[0.2em] text-[#ff2020]">{label}</span>
      <div className="flex-1 h-px bg-[rgba(255,32,32,0.2)]" />
    </div>
  )
}

// ── Helpers ────────────────────────────────────────────────────────────────

type FilterStatus = 'all' | 'pending' | 'approved' | 'rejected'

const FILTERS: { label: string; value: FilterStatus }[] = [
  { label: 'ALL', value: 'all' },
  { label: 'PENDING', value: 'pending' },
  { label: 'APPROVED', value: 'approved' },
  { label: 'REJECTED', value: 'rejected' },
]

const RISK_CONFIG = {
  low:    { label: 'LOW',    color: '#00ff88', bg: 'rgba(0,255,136,0.08)',  border: 'rgba(0,255,136,0.35)' },
  medium: { label: 'MEDIUM', color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', border: 'rgba(245,158,11,0.35)' },
  high:   { label: 'HIGH',   color: '#ff2020', bg: 'rgba(255,32,32,0.08)',  border: 'rgba(255,32,32,0.35)' },
}

const STATUS_CONFIG = {
  pending:  { label: 'PENDING',  color: '#ff2020', bg: 'rgba(255,32,32,0.08)',  border: 'rgba(255,32,32,0.35)' },
  approved: { label: 'APPROVED', color: '#00ff88', bg: 'rgba(0,255,136,0.08)',  border: 'rgba(0,255,136,0.35)' },
  rejected: { label: 'REJECTED', color: '#ef4444', bg: 'rgba(239,68,68,0.08)',  border: 'rgba(239,68,68,0.35)' },
}

function RiskBadge({ level }: { level: Approval['risk_level'] }) {
  const cfg = RISK_CONFIG[level]
  return (
    <span
      className="font-mono text-[9px] font-semibold tracking-[0.15em] px-2 py-0.5 rounded"
      style={{ color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.border}` }}
    >
      {cfg.label}
    </span>
  )
}

function StatusBadge({ status }: { status: Approval['status'] }) {
  const cfg = STATUS_CONFIG[status]
  return (
    <span
      className="font-mono text-[9px] font-semibold tracking-[0.15em] px-2 py-0.5 rounded"
      style={{ color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.border}` }}
    >
      {cfg.label}
    </span>
  )
}

// ── Approval Card ──────────────────────────────────────────────────────────

function ApprovalRow({
  approval,
  onApprove,
  onReject,
}: {
  approval: Approval
  onApprove?: (id: string) => void
  onReject?: (id: string) => void
}) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)

  async function handleApprove() {
    if (!onApprove) return
    setBusy('approve')
    try { await onApprove(approval.id) } finally { setBusy(null) }
  }

  async function handleReject() {
    if (!onReject) return
    setBusy('reject')
    try { await onReject(approval.id) } finally { setBusy(null) }
  }

  return (
    <CyberPanel>
      {/* Top row */}
      <div className="flex flex-wrap items-start gap-2 mb-2">
        <span className="font-sans font-semibold text-sm text-text-primary flex-1 min-w-0">{approval.title}</span>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <RiskBadge level={approval.risk_level} />
          <StatusBadge status={approval.status} />
        </div>
      </div>

      {/* Description */}
      <p className="font-mono text-xs text-text-secondary mb-3 leading-relaxed">{approval.description}</p>

      {/* Action type */}
      <div className="mb-3">
        <span className="font-mono text-[9px] tracking-[0.15em] text-[#ff2020] uppercase">
          ACTION_TYPE:
        </span>{' '}
        <span className="font-mono text-[10px] text-text-muted uppercase tracking-wide">{approval.action_type}</span>
      </div>

      {/* Bottom row */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <span className="font-mono text-[10px] text-text-muted">
          {formatRelativeTime(approval.created_at)}
        </span>

        {approval.status === 'pending' && (
          <div className="flex gap-2">
            <button
              onClick={handleReject}
              disabled={busy !== null}
              className={cn(
                'font-mono text-[10px] tracking-[0.12em] px-3 py-1.5 rounded transition-colors',
                'border border-[rgba(239,68,68,0.4)] text-[#ef4444] hover:bg-[rgba(239,68,68,0.1)]',
                'disabled:opacity-40 disabled:cursor-not-allowed',
              )}
            >
              {busy === 'reject' ? 'REJECTING...' : 'REJECT'}
            </button>
            <button
              onClick={handleApprove}
              disabled={busy !== null}
              className={cn(
                'font-mono text-[10px] tracking-[0.12em] px-3 py-1.5 rounded transition-colors',
                'bg-[rgba(0,255,136,0.12)] border border-[rgba(0,255,136,0.4)] text-[#00ff88]',
                'hover:bg-[rgba(0,255,136,0.2)]',
                'disabled:opacity-40 disabled:cursor-not-allowed',
              )}
            >
              {busy === 'approve' ? 'APPROVING...' : 'APPROVE'}
            </button>
          </div>
        )}
      </div>
    </CyberPanel>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function ApprovalsPage() {
  const [filter, setFilter] = useState<FilterStatus>('all')
  const { data: approvals, loading, error, approve, reject } = useApprovals(filter)

  const pendingCount = approvals?.filter((a) => a.status === 'pending').length ?? 0

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      {/* Header */}
      <div>
        <SectionTitle label="PENDING APPROVAL QUEUE" live />
        <div className="flex items-center gap-3 mt-1">
          {pendingCount > 0 && (
            <span className="font-mono text-[10px] text-[#ff2020] border border-[rgba(255,32,32,0.4)] px-2 py-0.5 rounded">
              {pendingCount} AWAITING REVIEW
            </span>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-1 flex-wrap">
        {FILTERS.map(({ label, value }) => (
          <button
            key={value}
            onClick={() => setFilter(value)}
            className={cn(
              'font-mono text-[10px] tracking-[0.15em] px-3 py-1.5 rounded transition-colors',
              filter === value
                ? 'bg-[rgba(255,32,32,0.15)] border border-[rgba(255,32,32,0.6)] text-[#ff2020]'
                : 'border border-[rgba(255,32,32,0.2)] text-text-muted hover:text-[#ff2020] hover:border-[rgba(255,32,32,0.4)]',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center gap-3 py-12 justify-center">
          <span className="h-4 w-4 rounded-full border-2 border-[#ff2020] border-t-transparent animate-spin" />
          <span className="font-mono text-xs text-[#ff2020] tracking-[0.2em]">LOADING APPROVAL QUEUE...</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <CyberPanel>
          <span className="font-mono text-xs text-[#ef4444]">
            ERROR: Failed to load approvals — {error.message}
          </span>
        </CyberPanel>
      )}

      {/* Empty */}
      {!loading && !error && (!approvals || approvals.length === 0) && (
        <div className="flex flex-col items-center gap-3 py-16">
          <span className="font-mono text-sm tracking-[0.25em] text-text-muted">NO PENDING APPROVALS</span>
          <span className="font-mono text-[10px] tracking-[0.3em] neon-green">◆ SYSTEM SECURE ◆</span>
        </div>
      )}

      {/* Cards */}
      {!loading && approvals && approvals.length > 0 && (
        <div className="space-y-3">
          {approvals.map((approval) => (
            <ApprovalRow
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
