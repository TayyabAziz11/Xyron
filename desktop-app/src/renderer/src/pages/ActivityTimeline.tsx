import { useEffect, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { RefreshCw, Mic, Globe, Youtube, Newspaper, DollarSign, Terminal, Clock, AlertCircle, TrendingUp } from 'lucide-react'

interface ActivityItem {
  id: string
  action: string
  description?: string
  status?: string
  created_at?: string
  source?: string
}

function getIconAndColor(action: string, status?: string): { icon: React.ReactNode; bg: string; border: string } {
  const a = (action || '').toLowerCase()
  const base = status === 'failed' ? { bg: 'bg-err/10', border: 'border-err/20' }
             : status === 'pending' ? { bg: 'bg-warn/10', border: 'border-warn/20' }
             : { bg: 'bg-brand/10', border: 'border-brand/20' }

  if (a.includes('youtube') || a.includes('play'))
    return { icon: <Youtube size={12} className="text-red-400" />, bg: 'bg-red-500/10', border: 'border-red-500/20' }
  if (a.includes('news'))
    return { icon: <Newspaper size={12} className="text-blue-400" />, bg: 'bg-blue-500/10', border: 'border-blue-500/20' }
  if (a.includes('price') || a.includes('bitcoin') || a.includes('stock') || a.includes('crypto'))
    return { icon: <DollarSign size={12} className="text-ok" />, bg: 'bg-ok/10', border: 'border-ok/20' }
  if (a.includes('search') || a.includes('google') || a.includes('open') || a.includes('url'))
    return { icon: <Globe size={12} className="text-sky-400" />, bg: 'bg-sky-500/10', border: 'border-sky-500/20' }
  if (a.includes('voice') || a.includes('speak') || a.includes('listen') || a.includes('mic'))
    return { icon: <Mic size={12} className="text-brand-light" />, ...base }
  if (a.includes('trend') || a.includes('analytic'))
    return { icon: <TrendingUp size={12} className="text-emerald-400" />, bg: 'bg-emerald-500/10', border: 'border-emerald-500/20' }
  return { icon: <Terminal size={12} className="text-slate-400" />, ...base }
}

function formatRelative(iso?: string): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function formatGroupDate(iso?: string): string {
  if (!iso) return 'Today'
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (d.toDateString() === today.toDateString()) return 'Today'
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
}

export default function ActivityTimeline() {
  const [items,   setItems]   = useState<ActivityItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(false)
    try {
      let data: { data?: ActivityItem[]; items?: ActivityItem[] } | null = null
      if (window.electronAPI) data = await window.electronAPI.getActivity() as typeof data
      else { const r = await fetch('http://localhost:8000/api/v1/activity?limit=40'); data = await r.json() }
      const list = data?.data ?? data?.items ?? []
      setItems(Array.isArray(list) ? list : [])
    } catch { setError(true); setItems([]) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  // Group by date
  const grouped = items.reduce<Record<string, ActivityItem[]>>((acc, item) => {
    const key = formatGroupDate(item.created_at)
    if (!acc[key]) acc[key] = []
    acc[key].push(item)
    return acc
  }, {})

  const stats = {
    total: items.length,
    completed: items.filter(i => i.status === 'completed').length,
    failed: items.filter(i => i.status === 'failed').length,
  }

  return (
    <div className="h-full flex flex-col bg-bg bg-grid overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border/60 bg-surface/30 backdrop-blur-sm flex-shrink-0">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h1 className="text-sm font-bold text-slate-100">Activity Timeline</h1>
            <p className="text-[11px] text-slate-600 mt-0.5">{items.length} logged actions</p>
          </div>
          <button onClick={load} className="btn-ghost flex items-center gap-2 text-xs">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>

        {/* Stats row */}
        {!loading && items.length > 0 && (
          <div className="grid grid-cols-3 gap-2">
            {[
              { label: 'Total',     val: stats.total,     color: 'text-slate-300' },
              { label: 'Completed', val: stats.completed, color: 'text-ok' },
              { label: 'Failed',    val: stats.failed,    color: 'text-err' },
            ].map(({ label, val, color }) => (
              <div key={label} className="bg-card/50 border border-border/40 rounded-lg px-3 py-2 text-center">
                <div className={`text-base font-bold tabular-nums ${color}`}>{val}</div>
                <div className="text-[10px] text-slate-600">{label}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading && items.length === 0 ? (
          <div className="space-y-4">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="flex gap-3 animate-pulse">
                <div className="w-7 h-7 rounded-lg bg-surface border border-border flex-shrink-0 mt-1" />
                <div className="flex-1 pt-1">
                  <div className="h-2.5 bg-border rounded w-1/2 mb-2" />
                  <div className="h-2 bg-border/60 rounded w-3/4" />
                </div>
              </div>
            ))}
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-slate-600">
            <AlertCircle size={32} className="opacity-25" />
            <p className="text-sm">Could not load activity</p>
            <button onClick={load} className="btn-ghost text-xs">Try again</button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-slate-600">
            <Clock size={32} className="opacity-25" />
            <p className="text-sm">No activity yet — start a voice session</p>
          </div>
        ) : (
          <div className="space-y-6">
            {Object.entries(grouped).map(([date, group]) => (
              <div key={date}>
                {/* Date label */}
                <div className="flex items-center gap-3 mb-3">
                  <span className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider">{date}</span>
                  <div className="flex-1 h-px bg-border/40" />
                  <span className="text-[10px] text-slate-700">{group.length}</span>
                </div>

                {/* Timeline items */}
                <div className="relative ml-3.5">
                  {/* Vertical line */}
                  <div className="absolute left-0 top-3 bottom-3 w-px bg-gradient-to-b from-border via-border/60 to-transparent" />

                  <div className="space-y-0.5">
                    {group.map((item, idx) => {
                      const { icon, bg, border } = getIconAndColor(item.action, item.status)
                      return (
                        <motion.div
                          key={item.id}
                          initial={{ opacity: 0, x: -10 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: idx * 0.025 }}
                          className="flex gap-3 group"
                        >
                          {/* Icon node */}
                          <div className={`relative z-10 w-7 h-7 rounded-lg border flex items-center justify-center flex-shrink-0 mt-1.5 -ml-3.5 transition-all duration-200 group-hover:scale-110 ${bg} ${border}`}>
                            {icon}
                          </div>

                          {/* Content */}
                          <div className="flex-1 min-w-0 py-2 pb-3 border-b border-border/20 last:border-0">
                            <div className="flex items-start justify-between gap-2">
                              <div className="flex-1 min-w-0">
                                <p className="text-sm text-slate-300 leading-snug group-hover:text-slate-100 transition-colors">
                                  {item.description ?? item.action}
                                </p>
                                {item.source && (
                                  <span className="inline-block text-[10px] text-slate-700 mt-0.5">
                                    via {item.source}
                                  </span>
                                )}
                              </div>
                              <div className="flex-shrink-0 flex flex-col items-end gap-1">
                                <span className="text-[10px] text-slate-700">{formatRelative(item.created_at)}</span>
                                {item.status && item.status !== 'completed' && (
                                  <span className={`text-[9px] font-semibold uppercase ${
                                    item.status === 'failed' ? 'text-err' :
                                    item.status === 'pending' ? 'text-warn' : 'text-blue-400'
                                  }`}>{item.status}</span>
                                )}
                              </div>
                            </div>
                          </div>
                        </motion.div>
                      )
                    })}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
