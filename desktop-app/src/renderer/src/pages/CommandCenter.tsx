import { useCallback, useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Zap, Check, X, Copy, RefreshCw, Clock, ChevronDown,
  Search, History, BookOpen, Layers, Trash2, Plus, ToggleLeft, ToggleRight,
} from 'lucide-react'
import { useHistory }  from '../hooks/useHistory'
import { useMacros }   from '../hooks/useMacros'
import { useNotes }    from '../hooks/useNotes'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Command {
  id: string
  text: string
  status: 'pending' | 'completed' | 'failed' | 'processing'
  assistant_response?: string
  result?: string
  created_at?: string
  source?: string
}

// ── Shared helpers ─────────────────────────────────────────────────────────────

const STATUS_CFG: Record<string, { label: string; style: string; dot: string }> = {
  pending:    { label: 'Pending',   style: 'bg-warn/10 text-warn border-warn/25',             dot: 'bg-warn' },
  processing: { label: 'Running',   style: 'bg-blue-500/10 text-blue-400 border-blue-500/25', dot: 'bg-blue-400 animate-pulse' },
  completed:  { label: 'Done',      style: 'bg-ok/10 text-ok border-ok/25',                   dot: 'bg-ok' },
  failed:     { label: 'Failed',    style: 'bg-err/10 text-err border-err/25',                dot: 'bg-err' },
}

function StatusPill({ status }: { status: string }) {
  const c = STATUS_CFG[status] ?? STATUS_CFG.pending
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold border ${c.style}`}>
      <span className={`w-1 h-1 rounded-full ${c.dot}`} />
      {c.label}
    </span>
  )
}

type Tab = 'commands' | 'history' | 'macros' | 'notes'

// ── Commands tab ───────────────────────────────────────────────────────────────

function CommandsTab() {
  const [commands, setCommands] = useState<Command[]>([])
  const [loading,  setLoading]  = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [copied,   setCopied]   = useState<string | null>(null)
  const [search,   setSearch]   = useState('')
  const [filter,   setFilter]   = useState<string>('all')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      let data: { data?: Command[]; items?: Command[] } | null = null
      if (window.electronAPI) {
        data = await window.electronAPI.getCommands() as typeof data
      } else {
        const r = await fetch('http://localhost:8000/api/v1/commands?limit=20')
        data = await r.json()
      }
      const items = data?.data ?? data?.items ?? []
      setCommands(Array.isArray(items) ? [...items].reverse() : [])
    } catch { setCommands([]) } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const approve = async (id: string) => {
    try {
      if (window.electronAPI) await window.electronAPI.approveCommand(id)
      else await fetch(`http://localhost:8000/api/v1/approvals/${id}/approve`, { method: 'POST' })
      load()
    } catch { /* ok */ }
  }

  const reject = async (id: string) => {
    try {
      if (window.electronAPI) await window.electronAPI.rejectCommand(id)
      else await fetch(`http://localhost:8000/api/v1/approvals/${id}/reject`, { method: 'POST' })
      load()
    } catch { /* ok */ }
  }

  const copy = async (text: string, id: string) => {
    try {
      if (window.electronAPI) await window.electronAPI.copyText(text)
      else await navigator.clipboard.writeText(text)
      setCopied(id); setTimeout(() => setCopied(null), 1500)
    } catch { /* ok */ }
  }

  const counts: Record<string, number> = { all: commands.length, completed: 0, failed: 0, pending: 0, processing: 0 }
  commands.forEach(c => { if (c.status in counts) counts[c.status]++ })
  const pending  = commands.filter(c => c.status === 'pending')
  const filtered = commands.filter(c => {
    const ms = !search || c.text.toLowerCase().includes(search.toLowerCase())
    const mf = filter === 'all' || c.status === filter
    return ms && mf
  })

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-4 py-3 border-b border-border/60 flex-shrink-0 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-slate-500">{commands.length} total</span>
          <button onClick={load} className="text-slate-500 hover:text-slate-300 transition-colors">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="relative">
          <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-600" />
          <input type="text" placeholder="Search…" value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-7 pr-3 py-1.5 bg-card/60 border border-border/60 rounded-lg text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-brand/40" />
        </div>
        <div className="flex gap-1 flex-wrap">
          {(['all', 'pending', 'completed', 'failed'] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`px-2 py-1 rounded-md text-[10px] font-medium border transition-all ${
                filter === f
                  ? 'bg-brand/15 border-brand/30 text-brand-light'
                  : 'bg-card/40 border-border/40 text-slate-500 hover:text-slate-300'
              }`}>
              {f === 'all' ? `All (${counts.all})` : `${f.charAt(0).toUpperCase() + f.slice(1)} (${counts[f]})`}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {pending.length > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-2">
              <div className="w-1.5 h-1.5 rounded-full bg-warn animate-pulse" />
              <span className="text-[10px] font-semibold text-warn uppercase tracking-wider">
                Awaiting Approval ({pending.length})
              </span>
            </div>
            <div className="space-y-2">
              {pending.map(cmd => (
                <div key={cmd.id} className="glass-card p-3">
                  <p className="text-sm text-slate-200 mb-2 leading-relaxed">{cmd.text}</p>
                  <div className="flex gap-2">
                    <button onClick={() => approve(cmd.id)}
                      className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-ok/15 text-ok border border-ok/25 text-xs font-semibold hover:bg-ok/25 transition-colors">
                      <Check size={10} /> Execute
                    </button>
                    <button onClick={() => reject(cmd.id)}
                      className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-err/10 text-err border border-err/25 text-xs font-semibold hover:bg-err/20 transition-colors">
                      <X size={10} /> Cancel
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {filtered.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-12 gap-3 text-slate-600">
            <Zap size={28} className="opacity-20" />
            <p className="text-xs">{search ? 'No matches' : 'No commands yet'}</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map(cmd => (
              <div key={cmd.id} className="glass-card overflow-hidden">
                <button className="w-full flex items-start gap-2.5 p-3 text-left hover:bg-white/[0.02] transition-colors"
                  onClick={() => setExpanded(expanded === cmd.id ? null : cmd.id)}>
                  <div className={`w-0.5 self-stretch rounded-full flex-shrink-0 ${
                    cmd.status === 'completed' ? 'bg-ok/50' : cmd.status === 'failed' ? 'bg-err/50' :
                    cmd.status === 'pending'   ? 'bg-warn/50' : 'bg-blue-400/50'
                  }`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 mb-1">
                      <StatusPill status={cmd.status} />
                      {cmd.source && (
                        <span className="text-[10px] text-slate-700 border border-border/40 px-1 py-0.5 rounded-full">
                          {cmd.source}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-slate-200 leading-snug">{cmd.text}</p>
                    {cmd.created_at && (
                      <p className="text-[10px] text-slate-700 mt-1 flex items-center gap-1">
                        <Clock size={8} />
                        {new Date(cmd.created_at).toLocaleString()}
                      </p>
                    )}
                  </div>
                  <motion.div animate={{ rotate: expanded === cmd.id ? 180 : 0 }} transition={{ duration: 0.2 }}
                    className="flex-shrink-0 mt-0.5">
                    <ChevronDown size={12} className="text-slate-600" />
                  </motion.div>
                </button>
                <AnimatePresence>
                  {expanded === cmd.id && (cmd.assistant_response || cmd.result) && (
                    <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }} className="border-t border-border/40">
                      <div className="p-3 bg-black/20 flex items-start justify-between gap-2">
                        <p className="text-xs text-slate-400 leading-relaxed flex-1">
                          {cmd.assistant_response ?? cmd.result}
                        </p>
                        <button onClick={() => copy(cmd.assistant_response ?? cmd.result ?? '', cmd.id)}
                          className="flex-shrink-0 p-1 rounded hover:bg-border/60 transition-colors">
                          <Copy size={10} className={copied === cmd.id ? 'text-ok' : 'text-slate-600'} />
                        </button>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── History tab ────────────────────────────────────────────────────────────────

function HistoryTab() {
  const { entries, loading, summary, loadToday, search } = useHistory()
  const [query, setQuery] = useState('')

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    if (query.trim()) search(query.trim())
    else loadToday()
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-4 py-3 border-b border-border/60 flex-shrink-0 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-slate-500">{entries.length} entries today</span>
          <button onClick={loadToday} className="text-slate-500 hover:text-slate-300">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        <form onSubmit={handleSearch} className="relative">
          <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-600" />
          <input type="text" placeholder="Search history…" value={query}
            onChange={e => setQuery(e.target.value)}
            className="w-full pl-7 pr-3 py-1.5 bg-card/60 border border-border/60 rounded-lg text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-brand/40" />
        </form>
        {summary && <p className="text-[11px] text-slate-500 italic leading-snug">{summary}</p>}
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
        {entries.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2 text-slate-600">
            <History size={28} className="opacity-20" />
            <p className="text-xs">No history yet</p>
          </div>
        ) : entries.map(e => (
          <div key={e.id} className="glass-card p-3">
            <div className="flex items-start justify-between gap-2 mb-1">
              <p className="text-xs text-slate-200 flex-1 leading-snug">{e.command}</p>
              <span className="text-[10px] text-slate-600 flex-shrink-0">{e.time_str}</span>
            </div>
            {e.result && (
              <p className="text-[11px] text-slate-500 leading-snug mt-1 line-clamp-2">{e.result}</p>
            )}
            {e.tool_used && (
              <span className="mt-1.5 inline-block text-[10px] text-brand-light/70 border border-brand/20 px-1.5 py-0.5 rounded-full">
                {e.tool_used}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Macros tab ─────────────────────────────────────────────────────────────────

function MacrosTab() {
  const { macros, loading, load, remove, toggle } = useMacros()
  const [showForm, setShowForm] = useState(false)
  const [trigger,  setTrigger]  = useState('')
  const [name,     setName]     = useState('')

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!trigger.trim() || !name.trim()) return
    try {
      await fetch('http://localhost:8000/api/v1/macros', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trigger: trigger.trim(), name: name.trim(), steps: [] }),
      })
      setTrigger(''); setName(''); setShowForm(false); load()
    } catch { /* ok */ }
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-4 py-3 border-b border-border/60 flex-shrink-0 flex items-center justify-between">
        <span className="text-[11px] text-slate-500">{macros.length} macros</span>
        <div className="flex items-center gap-2">
          <button onClick={load} className="text-slate-500 hover:text-slate-300">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
          <button onClick={() => setShowForm(v => !v)}
            className="flex items-center gap-1 px-2 py-1 rounded-lg bg-brand/10 border border-brand/20 text-xs text-brand-light hover:bg-brand/20 transition-colors">
            <Plus size={10} /> New
          </button>
        </div>
      </div>

      <AnimatePresence>
        {showForm && (
          <motion.form onSubmit={handleCreate}
            initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            className="px-4 py-3 border-b border-border/60 space-y-2 overflow-hidden">
            <input type="text" placeholder='Trigger phrase (e.g. "start my day")' value={trigger}
              onChange={e => setTrigger(e.target.value)}
              className="w-full px-3 py-1.5 bg-card/60 border border-border/60 rounded-lg text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-brand/40" />
            <input type="text" placeholder="Macro name" value={name}
              onChange={e => setName(e.target.value)}
              className="w-full px-3 py-1.5 bg-card/60 border border-border/60 rounded-lg text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-brand/40" />
            <button type="submit"
              className="w-full py-1.5 rounded-lg bg-brand/15 border border-brand/30 text-xs text-brand-light hover:bg-brand/25 transition-colors">
              Create Macro
            </button>
          </motion.form>
        )}
      </AnimatePresence>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
        {macros.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2 text-slate-600">
            <Layers size={28} className="opacity-20" />
            <p className="text-xs">No macros yet — create one above</p>
          </div>
        ) : macros.map(m => (
          <div key={m.id} className="glass-card p-3 flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-0.5">
                <span className="text-xs font-semibold text-slate-200 truncate">{m.name}</span>
                {m.run_count > 0 && (
                  <span className="text-[10px] text-slate-600">{m.run_count}×</span>
                )}
              </div>
              <p className="text-[11px] text-slate-500 truncate">"{m.trigger}"</p>
              <p className="text-[10px] text-slate-700 mt-0.5">{m.steps.length} steps</p>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button onClick={() => toggle(m.id, !m.active)} className="text-slate-600 hover:text-slate-300 transition-colors">
                {m.active
                  ? <ToggleRight size={16} className="text-ok" />
                  : <ToggleLeft  size={16} />}
              </button>
              <button onClick={() => remove(m.id)} className="text-slate-600 hover:text-err transition-colors">
                <Trash2 size={12} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Notes tab ──────────────────────────────────────────────────────────────────

function NotesTab() {
  const { notes, searchResults, loading, load, add, search, remove } = useNotes()
  const [query,     setQuery]     = useState('')
  const [newNote,   setNewNote]   = useState('')
  const [searching, setSearching] = useState(false)

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim()) { setSearching(false); load(); return }
    setSearching(true)
    await search(query.trim())
  }

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newNote.trim()) return
    await add(newNote.trim())
    setNewNote('')
  }

  const displayed = searching ? searchResults : notes

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-4 py-3 border-b border-border/60 flex-shrink-0 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-slate-500">{notes.length} notes</span>
          <button onClick={() => { setSearching(false); setQuery(''); load() }}
            className="text-slate-500 hover:text-slate-300">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        <form onSubmit={handleSearch} className="relative">
          <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-600" />
          <input type="text" placeholder="Semantic search…" value={query}
            onChange={e => setQuery(e.target.value)}
            className="w-full pl-7 pr-3 py-1.5 bg-card/60 border border-border/60 rounded-lg text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-brand/40" />
        </form>
        <form onSubmit={handleAdd} className="flex gap-2">
          <input type="text" placeholder="Add a note…" value={newNote}
            onChange={e => setNewNote(e.target.value)}
            className="flex-1 px-3 py-1.5 bg-card/60 border border-border/60 rounded-lg text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-brand/40" />
          <button type="submit"
            className="px-2.5 py-1.5 rounded-lg bg-brand/15 border border-brand/30 text-xs text-brand-light hover:bg-brand/25 transition-colors flex-shrink-0">
            <Plus size={11} />
          </button>
        </form>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
        {displayed.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2 text-slate-600">
            <BookOpen size={28} className="opacity-20" />
            <p className="text-xs">{searching ? 'No results' : 'No notes yet'}</p>
          </div>
        ) : displayed.map(n => (
          <div key={n.id} className="glass-card p-3 flex items-start gap-2">
            <div className="flex-1 min-w-0">
              <p className="text-xs text-slate-200 leading-snug">{n.text}</p>
              <p className="text-[10px] text-slate-600 mt-1">{n.date_str} {n.time_str}</p>
            </div>
            <button onClick={() => remove(n.id)}
              className="text-slate-700 hover:text-err transition-colors flex-shrink-0 mt-0.5">
              <Trash2 size={11} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────

const TABS: { id: Tab; label: string; icon: React.ElementType }[] = [
  { id: 'commands', label: 'Commands', icon: Zap },
  { id: 'history',  label: 'History',  icon: History },
  { id: 'macros',   label: 'Macros',   icon: Layers },
  { id: 'notes',    label: 'Notes',    icon: BookOpen },
]

export default function CommandCenter() {
  const [tab, setTab] = useState<Tab>('commands')

  return (
    <div className="h-full flex flex-col bg-bg bg-grid overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border/60 bg-surface/30 backdrop-blur-sm flex-shrink-0">
        <h1 className="text-sm font-bold text-slate-100">Command Center</h1>
        <p className="text-[11px] text-slate-600 mt-0.5">Commands · History · Macros · Notes</p>
      </div>

      {/* Tab bar */}
      <div className="flex px-4 pt-3 gap-1 border-b border-border/60 bg-surface/20 flex-shrink-0">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-t-lg text-[11px] font-medium border-b-2 transition-all duration-200 ${
              tab === id
                ? 'border-brand text-brand-light bg-brand/8'
                : 'border-transparent text-slate-500 hover:text-slate-300 hover:bg-white/[0.03]'
            }`}
          >
            <Icon size={11} />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -8 }}
            transition={{ duration: 0.15 }}
            className="h-full"
          >
            {tab === 'commands' && <CommandsTab />}
            {tab === 'history'  && <HistoryTab />}
            {tab === 'macros'   && <MacrosTab />}
            {tab === 'notes'    && <NotesTab />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}
