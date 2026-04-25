'use client'

import { useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import { Clock, Search, RefreshCw, Terminal, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { PageTransition } from '@/components/layout/PageTransition'

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface MemoryTurn {
  id:         number
  session_id: string
  role:       'user' | 'assistant'
  content:    string
  tool:       string | null
  ts:         number
}

function fmtTime(ts: number) {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function fmtDate(ts: number) {
  const d = new Date(ts * 1000)
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
}

function groupByDate(entries: MemoryTurn[]) {
  const groups: Record<string, MemoryTurn[]> = {}
  for (const e of entries) {
    const key = fmtDate(e.ts)
    ;(groups[key] ??= []).push(e)
  }
  return groups
}

export default function HistoryPage() {
  const [entries,  setEntries]  = useState<MemoryTurn[]>([])
  const [loading,  setLoading]  = useState(false)
  const [hours,    setHours]    = useState(24)
  const [search,   setSearch]   = useState('')

  const load = useCallback(async (h: number) => {
    setLoading(true)
    try {
      const r    = await fetch(`${API_BASE}/api/v1/memory/history?hours=${h}&limit=200`)
      const data = await r.json()
      setEntries(data.entries ?? [])
    } catch { /* silent */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { load(hours) }, [hours, load])

  const filtered = search.trim()
    ? entries.filter(e =>
        e.content.toLowerCase().includes(search.toLowerCase()) ||
        (e.tool ?? '').toLowerCase().includes(search.toLowerCase())
      )
    : entries

  const groups = groupByDate(filtered)

  return (
    <PageTransition>
      <div className="flex h-screen flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-surface-border px-6 py-4">
          <div className="flex items-center gap-3">
            <Clock className="h-5 w-5 text-brand" />
            <h1 className="text-lg font-semibold text-text-primary">Conversation History</h1>
          </div>
          <div className="flex items-center gap-3">
            {/* Hours filter */}
            <select
              value={hours}
              onChange={e => setHours(Number(e.target.value))}
              className="rounded-lg border border-surface-border bg-surface-raised px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:ring-1 focus:ring-brand"
            >
              <option value={6}>Last 6h</option>
              <option value={24}>Last 24h</option>
              <option value={72}>Last 3d</option>
              <option value={168}>Last 7d</option>
            </select>
            <button
              onClick={() => load(hours)}
              className="flex items-center gap-1.5 rounded-lg border border-surface-border bg-surface-raised px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary transition-colors"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        {/* Search */}
        <div className="border-b border-surface-border px-6 py-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-muted" />
            <input
              type="text"
              placeholder="Filter by message or tool…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full rounded-lg border border-surface-border bg-surface-raised py-2 pl-9 pr-4 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-brand"
            />
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-6">
          {loading && entries.length === 0 && (
            <div className="flex items-center justify-center py-16 text-text-muted">
              <Loader2 className="h-5 w-5 animate-spin mr-2" />
              Loading history…
            </div>
          )}

          {!loading && filtered.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 text-text-muted">
              <Clock className="h-8 w-8 mb-3 opacity-40" />
              <p className="text-sm">No conversation history found.</p>
            </div>
          )}

          {Object.entries(groups).map(([date, turns]) => (
            <div key={date}>
              <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-text-muted">{date}</p>
              <div className="space-y-2">
                {turns.map(turn => (
                  <motion.div
                    key={turn.id}
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    className={`flex gap-3 rounded-xl border px-4 py-3 ${
                      turn.role === 'user'
                        ? 'border-surface-border bg-surface-raised'
                        : 'border-brand/20 bg-brand/5'
                    }`}
                  >
                    <div className="flex-shrink-0 mt-0.5">
                      {turn.role === 'user' ? (
                        <div className="h-5 w-5 rounded-full bg-surface-overlay flex items-center justify-center text-[10px] font-bold text-text-muted">U</div>
                      ) : (
                        <div className="h-5 w-5 rounded-full bg-brand/20 flex items-center justify-center text-[10px] font-bold text-brand">X</div>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-text-primary leading-relaxed">{turn.content}</p>
                      {turn.tool && (
                        <div className="mt-1.5 flex items-center gap-1.5">
                          <Terminal className="h-3 w-3 text-text-muted" />
                          <span className="text-xs font-mono text-text-muted">{turn.tool}</span>
                        </div>
                      )}
                    </div>
                    <span className="flex-shrink-0 text-xs text-text-muted mt-0.5">{fmtTime(turn.ts)}</span>
                  </motion.div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </PageTransition>
  )
}
