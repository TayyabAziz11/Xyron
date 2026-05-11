'use client'

import { useState, useEffect, useCallback } from 'react'
import { Search, RefreshCw, Terminal, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import type { Command } from '@/lib/types'
import { cn } from '@/lib/utils'

const API_BASE = typeof window !== 'undefined' ? '' : 'http://localhost:8000'

interface HistoryEntry {
  id: string; role: 'user' | 'assistant'; content: string
  timestamp: string; command_id?: string; status?: string
}

const relTime = (iso: string) => {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60)   return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ago`
}

function CyberPanel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`cyber-panel p-4 ${className}`}>{children}</div>
}

function ST({ label, live = false }: { label: string; live?: boolean }) {
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

const STATUS_ICON: Record<string, React.ReactNode> = {
  completed: <CheckCircle2 className="h-3.5 w-3.5 text-[#00ff88]" />,
  failed:    <XCircle      className="h-3.5 w-3.5 text-[#ef4444]" />,
  running:   <Loader2      className="h-3.5 w-3.5 text-[#ff2020] animate-spin" />,
  queued:    <Loader2      className="h-3.5 w-3.5 text-text-muted animate-spin" />,
}

const STATUS_COLOR: Record<string, string> = {
  completed: '#00ff88',
  failed:    '#ef4444',
  running:   '#ff2020',
  queued:    '#94a3b8',
}

export default function HistoryPage() {
  const [commands, setCommands] = useState<Command[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [query, setQuery]       = useState('')
  const [page, setPage]         = useState(1)
  const PER_PAGE = 20

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const data = await api.commands.list(200)
      setCommands(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load history')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const filtered = commands.filter(c =>
    !query || c.text.toLowerCase().includes(query.toLowerCase()) ||
    c.agent?.toLowerCase().includes(query.toLowerCase())
  )
  const total   = filtered.length
  const paged   = filtered.slice(0, page * PER_PAGE)
  const hasMore = paged.length < total

  return (
    <div className="space-y-5 p-5">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-sm font-black tracking-[0.2em] text-[#ff2020]">COMMAND HISTORY</h1>
          <p className="font-mono text-[10px] text-text-muted">Full audit trail of all AI commands</p>
        </div>
        <button onClick={load}
          className="flex items-center gap-2 rounded border border-[rgba(255,32,32,0.3)] px-3 py-1.5 font-mono text-[10px] text-[#ff2020] transition-all hover:bg-[rgba(255,32,32,0.08)]">
          <RefreshCw className={cn('h-3 w-3', loading && 'animate-spin')} /> REFRESH
        </button>
      </div>

      {/* Search */}
      <div className="flex items-center gap-2 rounded border border-[rgba(255,32,32,0.3)] bg-[rgba(255,32,32,0.03)] px-3 py-2">
        <Search className="h-4 w-4 flex-shrink-0 text-[#ff2020]" />
        <input
          value={query}
          onChange={e => { setQuery(e.target.value); setPage(1) }}
          placeholder="Search commands, agents..."
          className="flex-1 bg-transparent font-mono text-[11px] text-text-secondary placeholder:text-text-muted focus:outline-none"
        />
        {query && (
          <button onClick={() => setQuery('')} className="font-mono text-[10px] text-text-muted hover:text-[#ff2020]">
            CLEAR
          </button>
        )}
      </div>

      {/* Stats row */}
      {!loading && (
        <div className="flex items-center gap-4 font-mono text-[10px]">
          <span className="text-text-muted">{total} COMMANDS</span>
          <span className="text-[#00ff88]">{commands.filter(c => c.status === 'completed').length} COMPLETED</span>
          <span className="text-[#ef4444]">{commands.filter(c => c.status === 'failed').length} FAILED</span>
        </div>
      )}

      {/* List */}
      {loading ? (
        <CyberPanel className="flex items-center justify-center gap-3 py-12">
          <Loader2 className="h-5 w-5 animate-spin text-[#ff2020]" />
          <span className="font-mono text-[11px] text-text-muted">LOADING HISTORY...</span>
        </CyberPanel>
      ) : error ? (
        <CyberPanel>
          <p className="font-mono text-[11px] text-[#ef4444]">ERROR: {error}</p>
        </CyberPanel>
      ) : paged.length === 0 ? (
        <CyberPanel className="flex flex-col items-center gap-3 py-12 text-center">
          <Terminal className="h-8 w-8 text-[rgba(255,32,32,0.3)]" />
          <p className="font-mono text-sm text-text-muted">NO COMMANDS FOUND</p>
          {query && <p className="font-mono text-[10px] text-text-muted">Try a different search term</p>}
        </CyberPanel>
      ) : (
        <div className="space-y-2">
          {paged.map(cmd => (
            <CyberPanel key={cmd.id} className="space-y-2">
              {/* Row 1: status + command */}
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex-shrink-0">{STATUS_ICON[cmd.status] ?? STATUS_ICON.queued}</div>
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-[12px] text-text-primary leading-snug break-words">{cmd.text}</p>
                  {cmd.assistant_response && (
                    <p className="mt-1 font-mono text-[11px] leading-relaxed text-text-muted line-clamp-2">
                      ↳ {cmd.assistant_response}
                    </p>
                  )}
                </div>
                {/* Status badge */}
                <span className="flex-shrink-0 rounded px-1.5 py-0.5 font-mono text-[9px] font-bold"
                  style={{
                    color: STATUS_COLOR[cmd.status] ?? '#94a3b8',
                    background: `${STATUS_COLOR[cmd.status] ?? '#94a3b8'}18`,
                    border: `1px solid ${STATUS_COLOR[cmd.status] ?? '#94a3b8'}40`,
                  }}>
                  {cmd.status.toUpperCase()}
                </span>
              </div>

              {/* Row 2: meta */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[rgba(255,32,32,0.08)] pt-2">
                <span className="font-mono text-[9px] text-text-muted">{relTime(cmd.created_at)}</span>
                {cmd.agent && (
                  <span className="font-mono text-[9px] text-[#00ffff]">
                    AGENT: {cmd.agent.toUpperCase()}
                  </span>
                )}
                {cmd.intent && (
                  <span className="font-mono text-[9px] text-text-muted">
                    {cmd.intent.skill}
                  </span>
                )}
                {cmd.error && (
                  <span className="font-mono text-[9px] text-[#ef4444] truncate max-w-xs">
                    ERR: {cmd.error}
                  </span>
                )}
              </div>
            </CyberPanel>
          ))}

          {hasMore && (
            <button onClick={() => setPage(p => p + 1)}
              className="w-full rounded border border-[rgba(255,32,32,0.3)] py-2.5 font-mono text-[11px] text-[#ff2020] transition-all hover:bg-[rgba(255,32,32,0.08)]">
              LOAD MORE ({total - paged.length} remaining)
            </button>
          )}
        </div>
      )}
    </div>
  )
}
