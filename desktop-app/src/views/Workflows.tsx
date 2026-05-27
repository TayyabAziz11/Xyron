
import { useState } from 'react'
import { useWorkflows } from '@/hooks/useWorkflows'
import { api } from '@/lib/api'
import type { Workflow } from '@/lib/types'
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

type WorkflowFilter = 'all' | 'active' | 'completed' | 'failed'

const FILTERS: { label: string; value: WorkflowFilter }[] = [
  { label: 'ALL',       value: 'all' },
  { label: 'ACTIVE',    value: 'active' },
  { label: 'COMPLETED', value: 'completed' },
  { label: 'FAILED',    value: 'failed' },
]

const STATUS_CONFIG: Record<
  Workflow['status'],
  { label: string; color: string; bg: string; border: string }
> = {
  active:    { label: 'ACTIVE',    color: '#00ffff', bg: 'rgba(0,255,255,0.08)',  border: 'rgba(0,255,255,0.35)' },
  completed: { label: 'COMPLETED', color: '#00ff88', bg: 'rgba(0,255,136,0.08)',  border: 'rgba(0,255,136,0.35)' },
  failed:    { label: 'FAILED',    color: '#ef4444', bg: 'rgba(239,68,68,0.08)',  border: 'rgba(239,68,68,0.35)' },
  queued:    { label: 'QUEUED',    color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', border: 'rgba(245,158,11,0.35)' },
  paused:    { label: 'PAUSED',    color: '#94a3b8', bg: 'rgba(148,163,184,0.08)',border: 'rgba(148,163,184,0.3)' },
}

function getDuration(w: Workflow): string {
  const start = new Date(w.started_at)
  const end   = w.completed_at ? new Date(w.completed_at) : new Date()
  if (isNaN(start.getTime())) return ''
  const diffSec = Math.floor((end.getTime() - start.getTime()) / 1000)
  if (diffSec < 60)   return `${diffSec}s`
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ${diffSec % 60}s`
  return `${Math.floor(diffSec / 3600)}h ${Math.floor((diffSec % 3600) / 60)}m`
}

// ── Workflow Card ──────────────────────────────────────────────────────────

function WorkflowCard({ workflow }: { workflow: Workflow }) {
  const cfg = STATUS_CONFIG[workflow.status]
  const duration = getDuration(workflow)
  const isActive    = workflow.status === 'active'
  const isCompleted = workflow.status === 'completed'
  const isFailed    = workflow.status === 'failed'

  // Progress fill: active=60%, completed=100%, failed=stopped at some %
  const progressPct = isCompleted ? 100 : isFailed ? 35 : isActive ? undefined : 0

  return (
    <CyberPanel>
      {/* Top row: name + status + type */}
      <div className="flex flex-wrap items-start gap-2 mb-3">
        <span className="font-sans font-semibold text-sm text-text-primary flex-1 min-w-0 leading-tight">
          {workflow.name}
        </span>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <span
            className="font-mono text-[9px] font-semibold tracking-[0.15em] px-2 py-0.5 rounded"
            style={{ color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.border}` }}
          >
            {cfg.label}
          </span>
          <span className="font-mono text-[9px] tracking-[0.12em] text-text-muted border border-[rgba(255,32,32,0.2)] px-2 py-0.5 rounded uppercase">
            {workflow.type}
          </span>
        </div>
      </div>

      {/* Active: current step */}
      {isActive && workflow.current_step && (
        <div className="mb-3 font-mono text-xs text-[#00ffff] flex items-center gap-1.5">
          <span className="text-[#00ffff]">►</span>
          <span className="tracking-wide">
            CURRENT STEP:{' '}
            <span className="font-semibold">{workflow.current_step}</span>
          </span>
        </div>
      )}

      {/* Failed: error message */}
      {isFailed && workflow.error && (
        <div className="mb-3 font-mono text-xs text-[#ef4444] bg-[rgba(239,68,68,0.06)] border border-[rgba(239,68,68,0.2)] rounded px-3 py-2">
          ERROR: {workflow.error}
        </div>
      )}

      {/* Progress bar */}
      <div className="mb-3 h-1 rounded-full bg-[rgba(255,32,32,0.08)] overflow-hidden">
        {isActive ? (
          /* Indeterminate animated bar for active */
          <div
            className="h-full rounded-full"
            style={{
              background: `linear-gradient(90deg, transparent 0%, ${cfg.color} 50%, transparent 100%)`,
              width: '40%',
              animation: 'progressScan 2s linear infinite',
            }}
          />
        ) : (
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{
              width: `${progressPct ?? 0}%`,
              background: isFailed
                ? 'rgba(239,68,68,0.6)'
                : isCompleted
                ? '#00ff88'
                : cfg.color,
            }}
          />
        )}
      </div>

      {/* Footer: agent + duration + timestamp */}
      <div className="flex flex-wrap items-center gap-3 text-[9px] font-mono text-text-muted">
        {workflow.agent && (
          <span className="uppercase tracking-[0.12em]">
            AGENT: <span className="text-text-secondary">{workflow.agent}</span>
          </span>
        )}
        {duration && (
          <span className="tracking-[0.12em]">
            DURATION: <span className="text-text-secondary">{duration}</span>
          </span>
        )}
        <span className="ml-auto tracking-[0.1em]">{formatRelativeTime(workflow.started_at)}</span>
      </div>
    </CyberPanel>
  )
}

// ── Inline keyframe for progress scan ─────────────────────────────────────

const SCAN_STYLE = (
  <style>{`
    @keyframes progressScan {
      0%   { transform: translateX(-200%); }
      100% { transform: translateX(500%); }
    }
  `}</style>
)

// ── Page ───────────────────────────────────────────────────────────────────

export default function WorkflowsPage() {
  const [filter, setFilter] = useState<WorkflowFilter>('all')
  const { data: workflows, loading, error } = useWorkflows(filter)

  const activeCount = workflows?.filter((w) => w.status === 'active').length ?? 0

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      {SCAN_STYLE}

      {/* Header */}
      <div>
        <SectionTitle label="ACTIVE AGENT WORKFLOWS" live={activeCount > 0} />
        {activeCount > 0 && (
          <span className="font-mono text-[10px] text-[#00ffff] border border-[rgba(0,255,255,0.3)] px-2 py-0.5 rounded">
            {activeCount} RUNNING
          </span>
        )}
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
          <span className="font-mono text-xs text-[#ff2020] tracking-[0.2em]">FETCHING WORKFLOWS...</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <CyberPanel>
          <span className="font-mono text-xs text-[#ef4444]">
            ERROR: Failed to load workflows — {error.message}
          </span>
        </CyberPanel>
      )}

      {/* Empty */}
      {!loading && !error && (!workflows || workflows.length === 0) && (
        <div className="flex flex-col items-center gap-3 py-16">
          <span className="font-mono text-sm tracking-[0.25em] text-text-muted">NO WORKFLOWS RUNNING</span>
          <span className="font-mono text-[10px] tracking-[0.3em] text-[#00ffff]">◆ SYSTEM IDLE ◆</span>
        </div>
      )}

      {/* Workflow cards */}
      {!loading && workflows && workflows.length > 0 && (
        <div className="space-y-3">
          {workflows.map((workflow) => (
            <WorkflowCard key={workflow.id} workflow={workflow} />
          ))}
        </div>
      )}
    </div>
  )
}
