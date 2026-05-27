

import { motion, AnimatePresence } from 'framer-motion'
import {
  CheckCircle2, XCircle, Loader2, Clock, ChevronDown,
  ChevronRight, X, AlertTriangle, Zap,
} from 'lucide-react'
import { useState } from 'react'
import type { Task, TaskStep } from '@/lib/types'

// ── Risk badge ────────────────────────────────────────────────────────────────

function RiskBadge({ level }: { level: string }) {
  if (level === 'low') return null
  return (
    <span
      className={[
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium',
        level === 'high'
          ? 'border-status-error/30 bg-status-error/10 text-status-error'
          : 'border-status-pending/30 bg-status-pending/10 text-status-pending',
      ].join(' ')}
    >
      <AlertTriangle className="h-3 w-3" />
      {level === 'high' ? 'High risk' : 'Confirm'}
    </span>
  )
}

// ── Tool label ────────────────────────────────────────────────────────────────

const TOOL_LABEL: Record<string, string> = {
  // System tools
  open_directory:   'Open folder',
  create_folder:    'Create folder',
  list_directory:   'List directory',
  open_file:        'Open file',
  open_application: 'Launch app',
  search_files:     'Search files',
  system_info:      'System info',
  write_file:       'Write file',
  // Web tools
  search_web:       'Search web',
  search_youtube:   'Search YouTube',
  open_url:         'Open URL',
  // Content tools
  compose_email:    'Compose email',
  create_post:      'Create post',
  get_summary:      'Get summary',
  list_approvals:   'List approvals',
  general_query:    'Process query',
}

// ── Step row ──────────────────────────────────────────────────────────────────

function StepRow({ step, isLast }: { step: TaskStep; isLast: boolean }) {
  const icon =
    step.status === 'completed' ? <CheckCircle2 className="h-4 w-4 text-status-success shrink-0" /> :
    step.status === 'failed'    ? <XCircle      className="h-4 w-4 text-status-error   shrink-0" /> :
    step.status === 'running'   ? <Loader2      className="h-4 w-4 text-brand-light animate-spin shrink-0" /> :
    step.status === 'skipped'   ? <ChevronRight className="h-4 w-4 text-text-muted     shrink-0" /> :
                                  <Clock        className="h-4 w-4 text-text-muted     shrink-0" />

  const rowColor =
    step.status === 'running'   ? 'border-brand/30   bg-brand/5' :
    step.status === 'completed' ? 'border-status-success/20 bg-status-success/5' :
    step.status === 'failed'    ? 'border-status-error/20   bg-status-error/5' :
                                  'border-surface-border     bg-surface-overlay'

  // Strip OPEN_URL/OPEN_APP prefix from display
  const displayResult = step.result
    ? step.result.replace(/^(OPEN_URL|OPEN_APP):[^|]*\|/, '')
    : null

  return (
    <div className="flex gap-3">
      {/* Timeline line */}
      <div className="flex flex-col items-center">
        <div className="mt-1">{icon}</div>
        {!isLast && <div className="w-px flex-1 bg-surface-border mt-1 mb-0.5" />}
      </div>

      {/* Content */}
      <div className={`flex-1 mb-2 rounded-lg border px-3 py-2 ${rowColor}`}>
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-text-primary truncate">
            {step.description}
          </span>
          <span className="shrink-0 text-xs text-text-muted">
            {TOOL_LABEL[step.tool] ?? step.tool}
          </span>
        </div>
        {displayResult && (
          <p className="mt-1 text-xs text-text-muted leading-relaxed line-clamp-2">
            {displayResult}
          </p>
        )}
        {step.error && (
          <p className="mt-1 text-xs text-status-error">{step.error}</p>
        )}
      </div>
    </div>
  )
}

// ── Single task card ──────────────────────────────────────────────────────────

function TaskCard({ task, onCancel }: { task: Task; onCancel?: (id: string) => void }) {
  const [expanded, setExpanded] = useState(task.status !== 'completed')

  const completed = task.steps.filter((s) => s.status === 'completed').length
  const total     = task.steps.length
  const pct       = total > 0 ? Math.round((completed / total) * 100) : 0

  const headerColor =
    task.status === 'completed' ? 'border-status-success/30 bg-status-success/5' :
    task.status === 'failed'    ? 'border-status-error/30   bg-status-error/5'   :
    task.status === 'planning'  ? 'border-surface-border    bg-surface-overlay'  :
                                  'border-brand/30          bg-brand/5'

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className={`rounded-xl border overflow-hidden ${headerColor}`}
    >
      {/* Header */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {task.status === 'running' || task.status === 'planning'
              ? <Loader2 className="h-3.5 w-3.5 text-brand-light animate-spin shrink-0" />
              : task.status === 'completed'
              ? <CheckCircle2 className="h-3.5 w-3.5 text-status-success shrink-0" />
              : task.status === 'failed'
              ? <XCircle className="h-3.5 w-3.5 text-status-error shrink-0" />
              : <Clock className="h-3.5 w-3.5 text-text-muted shrink-0" />}
            <p className="text-sm font-medium text-text-primary truncate">{task.goal}</p>
            <RiskBadge level={task.risk_level} />
          </div>
          {total > 0 && (
            <div className="mt-1.5 flex items-center gap-2">
              <div className="flex-1 h-1 rounded-full bg-surface-border overflow-hidden">
                <motion.div
                  className={`h-full rounded-full ${
                    task.status === 'failed' ? 'bg-status-error' :
                    task.status === 'completed' ? 'bg-status-success' : 'bg-brand-light'
                  }`}
                  initial={{ width: 0 }}
                  animate={{ width: `${pct}%` }}
                  transition={{ duration: 0.4 }}
                />
              </div>
              <span className="text-xs text-text-muted shrink-0">
                {task.status === 'planning' ? 'Planning…' : `${completed}/${total}`}
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {(task.status === 'running' || task.status === 'planning') && onCancel && (
            <button
              onClick={(e) => { e.stopPropagation(); onCancel(task.id) }}
              className="rounded p-1 text-text-muted hover:text-status-error hover:bg-status-error/10 transition-colors"
              title="Cancel task"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
          {expanded
            ? <ChevronDown  className="h-4 w-4 text-text-muted" />
            : <ChevronRight className="h-4 w-4 text-text-muted" />}
        </div>
      </div>

      {/* Steps */}
      <AnimatePresence initial={false}>
        {expanded && task.steps.length > 0 && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-3 pt-1 border-t border-surface-border/60">
              {task.steps.map((step, i) => (
                <StepRow key={step.id} step={step} isLast={i === task.steps.length - 1} />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Summary result */}
      {task.status === 'completed' && task.result && !expanded && (
        <div className="px-4 pb-3 text-xs text-text-muted border-t border-surface-border/60 pt-2">
          {task.result}
        </div>
      )}
      {task.status === 'failed' && task.error && (
        <div className="px-4 pb-3 text-xs text-status-error border-t border-surface-border/60 pt-2">
          {task.error}
        </div>
      )}
    </motion.div>
  )
}

// ── Panel ─────────────────────────────────────────────────────────────────────

interface TaskProgressPanelProps {
  tasks:    Task[]
  onCancel: (id: string) => void
  onSubmit?: (goal: string) => void
}

export function TaskProgressPanel({ tasks, onCancel, onSubmit }: TaskProgressPanelProps) {
  const [goal, setGoal] = useState('')

  if (tasks.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <Zap className="h-7 w-7 text-text-muted mb-2 opacity-40" />
        <p className="text-sm text-text-muted">No tasks yet</p>
        <p className="text-xs text-text-muted mt-0.5 opacity-70">
          Type a multi-step goal below
        </p>
        {onSubmit && (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              const g = goal.trim()
              if (!g) return
              onSubmit(g)
              setGoal('')
            }}
            className="mt-4 flex w-full gap-2"
          >
            <input
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="e.g. Search AI news and email it to John"
              className="flex-1 rounded-lg border border-surface-border bg-surface-overlay px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-brand/50 focus:outline-none focus:ring-1 focus:ring-brand/20"
            />
            <button
              type="submit"
              disabled={!goal.trim()}
              className="rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-light transition-colors disabled:opacity-40"
            >
              Run
            </button>
          </form>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {onSubmit && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            const g = goal.trim()
            if (!g) return
            onSubmit(g)
            setGoal('')
          }}
          className="flex gap-2"
        >
          <input
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="New multi-step goal…"
            className="flex-1 rounded-lg border border-surface-border bg-surface-overlay px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-brand/50 focus:outline-none focus:ring-1 focus:ring-brand/20"
          />
          <button
            type="submit"
            disabled={!goal.trim()}
            className="rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-light transition-colors disabled:opacity-40"
          >
            Run
          </button>
        </form>
      )}

      <div className="flex flex-col gap-2 max-h-[480px] overflow-y-auto pr-0.5">
        <AnimatePresence mode="popLayout">
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} onCancel={onCancel} />
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
