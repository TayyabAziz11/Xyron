'use client'

import { useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import { BarChart2, Zap, Clock, TrendingUp, RefreshCw, Loader2 } from 'lucide-react'
import { PageTransition } from '@/components/layout/PageTransition'

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface StatsData {
  tool_frequency:  Record<string, number>
  top_tools_now:   string[]
  activity_24h:    string
  hourly_patterns: Record<string, Record<string, number>>
  error?:          string
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border border-surface-border bg-surface-raised p-4">
      <p className="text-xs font-medium uppercase tracking-wider text-text-muted mb-1">{label}</p>
      <p className="text-2xl font-bold text-text-primary">{value}</p>
      {sub && <p className="text-xs text-text-muted mt-0.5">{sub}</p>}
    </div>
  )
}

function ToolBar({ tool, count, max }: { tool: string; count: number; max: number }) {
  const pct = max > 0 ? (count / max) * 100 : 0
  return (
    <div className="flex items-center gap-3">
      <span className="w-36 truncate text-xs font-mono text-text-secondary">{tool}</span>
      <div className="flex-1 h-2 rounded-full bg-surface-overlay overflow-hidden">
        <motion.div
          className="h-full rounded-full bg-brand"
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.5, ease: 'easeOut' }}
        />
      </div>
      <span className="w-8 text-right text-xs text-text-muted">{count}</span>
    </div>
  )
}

export default function StatsPage() {
  const [stats,   setStats]   = useState<StatsData | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r    = await fetch(`${API_BASE}/api/v1/memory/stats`)
      const data = await r.json()
      setStats(data)
    } catch { /* silent */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const freq       = stats?.tool_frequency ?? {}
  const sortedFreq = Object.entries(freq).sort((a, b) => b[1] - a[1])
  const maxCount   = sortedFreq[0]?.[1] ?? 1
  const totalCalls = sortedFreq.reduce((s, [, c]) => s + c, 0)
  const uniqueTools = sortedFreq.length

  return (
    <PageTransition>
      <div className="flex h-screen flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-surface-border px-6 py-4">
          <div className="flex items-center gap-3">
            <BarChart2 className="h-5 w-5 text-brand" />
            <h1 className="text-lg font-semibold text-text-primary">Usage Stats</h1>
          </div>
          <button
            onClick={load}
            className="flex items-center gap-1.5 rounded-lg border border-surface-border bg-surface-raised px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary transition-colors"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          {loading && !stats && (
            <div className="flex items-center justify-center py-16 text-text-muted">
              <Loader2 className="h-5 w-5 animate-spin mr-2" />
              Loading stats…
            </div>
          )}

          {stats && (
            <>
              {/* Summary cards */}
              <div className="grid grid-cols-3 gap-4">
                <StatCard label="Total Commands" value={totalCalls} sub="all time" />
                <StatCard label="Unique Tools" value={uniqueTools} sub="ever used" />
                <StatCard label="Active Now" value={stats.top_tools_now.length > 0 ? stats.top_tools_now[0] : '—'} sub="top tool this hour" />
              </div>

              {/* Activity summary */}
              {stats.activity_24h && (
                <div className="rounded-xl border border-brand/20 bg-brand/5 px-4 py-3">
                  <div className="flex items-center gap-2 mb-1.5">
                    <TrendingUp className="h-4 w-4 text-brand" />
                    <span className="text-xs font-semibold uppercase tracking-wider text-brand">24h Summary</span>
                  </div>
                  <p className="text-sm text-text-primary leading-relaxed">{stats.activity_24h}</p>
                </div>
              )}

              {/* Top tools now */}
              {stats.top_tools_now.length > 0 && (
                <div>
                  <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-text-muted">Top Tools This Hour</p>
                  <div className="flex flex-wrap gap-2">
                    {stats.top_tools_now.map((t, i) => (
                      <span
                        key={t}
                        className={`rounded-full px-3 py-1 text-xs font-mono font-medium border ${
                          i === 0
                            ? 'border-brand/40 bg-brand/10 text-brand'
                            : 'border-surface-border bg-surface-raised text-text-secondary'
                        }`}
                      >
                        {i === 0 && <Zap className="inline h-3 w-3 mr-1 -mt-0.5" />}
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Tool frequency chart */}
              {sortedFreq.length > 0 && (
                <div>
                  <p className="mb-4 text-xs font-semibold uppercase tracking-wider text-text-muted">Tool Usage Frequency</p>
                  <div className="space-y-3">
                    {sortedFreq.slice(0, 15).map(([tool, count]) => (
                      <ToolBar key={tool} tool={tool} count={count} max={maxCount} />
                    ))}
                  </div>
                </div>
              )}

              {/* Hourly patterns heatmap (top 5 tools) */}
              {Object.keys(stats.hourly_patterns).length > 0 && (
                <div>
                  <p className="mb-4 text-xs font-semibold uppercase tracking-wider text-text-muted">Hourly Patterns (last 7 days)</p>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr>
                          <th className="text-left pr-3 pb-2 font-medium text-text-muted w-36">Tool</th>
                          {Array.from({ length: 24 }, (_, h) => (
                            <th key={h} className="pb-2 font-medium text-text-muted text-center w-7">
                              {h % 6 === 0 ? `${h}h` : ''}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(stats.hourly_patterns)
                          .sort((a, b) =>
                            Object.values(b[1]).reduce((s, v) => s + v, 0) -
                            Object.values(a[1]).reduce((s, v) => s + v, 0)
                          )
                          .slice(0, 8)
                          .map(([tool, hours]) => {
                            const hourMax = Math.max(...Object.values(hours), 1)
                            return (
                              <tr key={tool}>
                                <td className="pr-3 py-0.5 font-mono text-text-secondary truncate max-w-[9rem]">{tool}</td>
                                {Array.from({ length: 24 }, (_, h) => {
                                  const cnt = hours[String(h).padStart(2, '0')] ?? 0
                                  const opacity = cnt > 0 ? 0.2 + (cnt / hourMax) * 0.8 : 0
                                  return (
                                    <td key={h} className="py-0.5 text-center">
                                      <div
                                        className="mx-auto h-4 w-5 rounded-sm"
                                        style={{ backgroundColor: cnt > 0 ? `rgba(99,102,241,${opacity})` : 'transparent' }}
                                        title={cnt > 0 ? `${tool} at ${h}:00 — ${cnt}×` : undefined}
                                      />
                                    </td>
                                  )
                                })}
                              </tr>
                            )
                          })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </PageTransition>
  )
}
