'use client'

import { useState } from 'react'
import { useActivity } from '@/hooks/useActivity'
import type { ActivityEvent } from '@/lib/types'
import { cn } from '@/lib/utils'
import { formatRelativeTime, formatAbsoluteTime } from '@/lib/utils'

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

type DayFilter = 1 | 7 | 30

const DAY_FILTERS: { label: string; value: DayFilter }[] = [
  { label: '24H', value: 1 },
  { label: '7D',  value: 7 },
  { label: '30D', value: 30 },
]

const STATUS_CONFIG: Record<
  ActivityEvent['status'],
  { color: string; bg: string; border: string; dot: string }
> = {
  success: { color: '#00ff88', bg: 'rgba(0,255,136,0.08)',  border: 'rgba(0,255,136,0.3)',  dot: '#00ff88' },
  error:   { color: '#ef4444', bg: 'rgba(239,68,68,0.08)',  border: 'rgba(239,68,68,0.3)',  dot: '#ef4444' },
  warning: { color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', border: 'rgba(245,158,11,0.3)', dot: '#f59e0b' },
  info:    { color: '#00ffff', bg: 'rgba(0,255,255,0.08)',  border: 'rgba(0,255,255,0.3)',  dot: '#00ffff' },
}

// ── Event Row ──────────────────────────────────────────────────────────────

function EventRow({ event, last }: { event: ActivityEvent; last: boolean }) {
  const cfg = STATUS_CONFIG[event.status]

  return (
    <li className={cn('flex items-start gap-3 py-3', !last && 'border-b border-[rgba(255,32,32,0.08)]')}>
      {/* Status dot + timestamp col */}
      <div className="flex flex-col items-center gap-1.5 pt-0.5 flex-shrink-0 w-28">
        <span
          className="h-2 w-2 rounded-full flex-shrink-0"
          style={{ background: cfg.dot, boxShadow: `0 0 6px ${cfg.dot}` }}
        />
        <span className="font-mono text-[9px] text-text-muted text-center leading-tight">
          {formatRelativeTime(event.timestamp)}
        </span>
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        {/* Event type label + description */}
        <div className="flex flex-wrap items-baseline gap-2 mb-0.5">
          <span
            className="font-mono text-[9px] font-semibold tracking-[0.18em] px-1.5 py-0.5 rounded flex-shrink-0"
            style={{ color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.border}` }}
          >
            {event.event_type.toUpperCase()}
          </span>
          <span className="font-mono text-xs text-text-secondary leading-relaxed">{event.description}</span>
        </div>

        {/* Absolute timestamp */}
        <span className="font-mono text-[9px] text-text-muted">
          {formatAbsoluteTime(event.timestamp)}
        </span>
      </div>

      {/* Source/actor badge */}
      {(event.source ?? event.actor) && (
        <div className="flex-shrink-0">
          <span className="font-mono text-[9px] tracking-[0.12em] px-2 py-0.5 rounded border border-[rgba(255,32,32,0.2)] text-text-muted uppercase">
            {event.actor ?? event.source}
          </span>
        </div>
      )}
    </li>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function ActivityPage() {
  const [days, setDays] = useState<DayFilter>(7)
  const [limit, setLimit] = useState(50)
  const { data: events, loading, error } = useActivity(limit, days)

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      {/* Header */}
      <SectionTitle label="AGENT ACTION AUDIT TRAIL" live />

      {/* Controls row */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Time range toggles */}
        <div className="flex gap-1">
          {DAY_FILTERS.map(({ label, value }) => (
            <button
              key={value}
              onClick={() => setDays(value)}
              className={cn(
                'font-mono text-[10px] tracking-[0.15em] px-3 py-1.5 rounded transition-colors',
                days === value
                  ? 'bg-[rgba(255,32,32,0.15)] border border-[rgba(255,32,32,0.6)] text-[#ff2020]'
                  : 'border border-[rgba(255,32,32,0.2)] text-text-muted hover:text-[#ff2020] hover:border-[rgba(255,32,32,0.4)]',
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Event count */}
        {events && (
          <span className="font-mono text-[10px] text-text-muted tracking-[0.15em] ml-auto">
            {events.length} EVENTS RECORDED
          </span>
        )}
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center gap-3 py-12 justify-center">
          <span className="h-4 w-4 rounded-full border-2 border-[#ff2020] border-t-transparent animate-spin" />
          <span className="font-mono text-xs text-[#ff2020] tracking-[0.2em]">SCANNING AUDIT LOG...</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <CyberPanel>
          <span className="font-mono text-xs text-[#ef4444]">
            ERROR: Failed to load activity — {error.message}
          </span>
        </CyberPanel>
      )}

      {/* Empty */}
      {!loading && !error && (!events || events.length === 0) && (
        <div className="flex flex-col items-center gap-3 py-16">
          <span className="font-mono text-sm tracking-[0.25em] text-text-muted">NO ACTIVITY RECORDED</span>
          <span className="font-mono text-[10px] tracking-[0.3em] text-[#00ffff]">◆ AWAITING AGENT EVENTS ◆</span>
        </div>
      )}

      {/* Event list */}
      {!loading && events && events.length > 0 && (
        <CyberPanel className="p-0">
          <ul className="px-4">
            {events.map((event, idx) => (
              <EventRow key={event.id} event={event} last={idx === events.length - 1} />
            ))}
          </ul>

          {/* Load more */}
          {events.length >= limit && (
            <div className="border-t border-[rgba(255,32,32,0.15)] p-4 text-center">
              <button
                onClick={() => setLimit((l) => l + 50)}
                className="font-mono text-[10px] tracking-[0.2em] px-4 py-2 rounded border border-[rgba(255,32,32,0.3)] text-[#ff2020] hover:bg-[rgba(255,32,32,0.08)] transition-colors"
              >
                LOAD MORE
              </button>
            </div>
          )}
        </CyberPanel>
      )}
    </div>
  )
}
