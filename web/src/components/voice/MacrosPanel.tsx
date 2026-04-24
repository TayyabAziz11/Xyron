'use client'

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, Trash2, ToggleLeft, ToggleRight, Zap } from 'lucide-react'
import { useMacros, type Macro } from '@/hooks/useMacros'

// ── Macro card ────────────────────────────────────────────────────────────────

function MacroCard({ macro, onToggle, onDelete }: {
  macro:    Macro
  onToggle: (id: number, active: boolean) => void
  onDelete: (id: number) => void
}) {
  const isActive = macro.active === 1
  return (
    <div className={`rounded-xl border p-3 transition-colors ${
      isActive ? 'border-accent-primary/20 bg-accent-primary/5' : 'border-surface-border bg-surface-secondary/30'
    }`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-text-primary truncate">{macro.name}</p>
          <p className="mt-0.5 text-xs text-text-muted">
            <span className="font-mono bg-surface-border/50 px-1 rounded">&quot;{macro.trigger}&quot;</span>
            {' '}→ {macro.steps.length} step{macro.steps.length !== 1 ? 's' : ''}
            {macro.run_count > 0 && <span className="ml-2 text-text-muted">· ran {macro.run_count}×</span>}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => onToggle(macro.id, !isActive)}
            className={`transition-colors ${isActive ? 'text-accent-primary' : 'text-text-muted hover:text-text-secondary'}`}
          >
            {isActive ? <ToggleRight className="h-5 w-5" /> : <ToggleLeft className="h-5 w-5" />}
          </button>
          <button
            onClick={() => onDelete(macro.id)}
            className="text-text-muted hover:text-status-error transition-colors"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>
      {macro.steps.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {macro.steps.map((s, i) => (
            <span key={i} className="rounded-full bg-surface-border/50 px-2 py-0.5 text-xs text-text-muted">
              {s.description || s.tool}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Create form ───────────────────────────────────────────────────────────────

function CreateMacroForm({ onCreate, onCancel }: {
  onCreate: (trigger: string, name: string) => void
  onCancel: () => void
}) {
  const [trigger, setTrigger] = useState('')
  const [name,    setName]    = useState('')

  return (
    <div className="rounded-xl border border-accent-primary/20 bg-accent-primary/5 p-3 space-y-2">
      <p className="text-xs font-medium text-accent-primary">New macro</p>
      <input
        className="w-full rounded-lg bg-surface-secondary border border-surface-border px-3 py-1.5 text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary/50"
        placeholder='Trigger phrase (e.g. "start my day")'
        value={trigger}
        onChange={e => setTrigger(e.target.value)}
      />
      <input
        className="w-full rounded-lg bg-surface-secondary border border-surface-border px-3 py-1.5 text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary/50"
        placeholder="Name (e.g. Morning Routine)"
        value={name}
        onChange={e => setName(e.target.value)}
      />
      <p className="text-xs text-text-muted">Steps can be added via voice: &quot;when I say &#39;{trigger || '...'}&#39;, open VS Code and read my emails&quot;</p>
      <div className="flex gap-2 pt-1">
        <button
          onClick={() => trigger && name && onCreate(trigger, name)}
          disabled={!trigger || !name}
          className="flex-1 rounded-lg bg-accent-primary/20 py-1.5 text-xs font-medium text-accent-primary hover:bg-accent-primary/30 transition-colors disabled:opacity-40"
        >
          Create
        </button>
        <button
          onClick={onCancel}
          className="flex-1 rounded-lg bg-surface-border/50 py-1.5 text-xs font-medium text-text-secondary hover:bg-surface-border transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────

export function MacrosPanel() {
  const { macros, loading, create, remove, toggle } = useMacros()
  const [creating, setCreating] = useState(false)

  const handleCreate = async (trigger: string, name: string) => {
    await create(trigger, name, [])
    setCreating(false)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Zap className="h-4 w-4 text-accent-primary" />
          <span className="text-sm font-medium text-text-primary">Voice Macros</span>
          <span className="text-xs text-text-muted">({macros.length})</span>
        </div>
        {!creating && (
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1 rounded-lg bg-accent-primary/10 px-2 py-1 text-xs font-medium text-accent-primary hover:bg-accent-primary/20 transition-colors"
          >
            <Plus className="h-3.5 w-3.5" /> New
          </button>
        )}
      </div>

      <AnimatePresence>
        {creating && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
          >
            <CreateMacroForm onCreate={handleCreate} onCancel={() => setCreating(false)} />
          </motion.div>
        )}
      </AnimatePresence>

      {loading && <p className="text-xs text-text-muted text-center py-2">Loading…</p>}

      {!loading && macros.length === 0 && !creating && (
        <p className="text-xs text-text-muted text-center py-4">
          No macros yet. Say <span className="font-mono">&quot;start my day&quot;</span> to try the built-in ones.
        </p>
      )}

      <div className="space-y-2">
        {macros.map(m => (
          <MacroCard
            key={m.id}
            macro={m}
            onToggle={toggle}
            onDelete={remove}
          />
        ))}
      </div>
    </div>
  )
}
