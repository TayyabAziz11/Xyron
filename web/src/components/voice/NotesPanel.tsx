'use client'

import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Search, Plus, Trash2, FileText, Loader2 } from 'lucide-react'
import { useNotes, type Note } from '@/hooks/useNotes'

function NoteCard({ note, onDelete }: { note: Note; onDelete: (id: number) => void }) {
  return (
    <div className="group rounded-xl border border-surface-border bg-surface-secondary/30 p-3 hover:border-accent-primary/20 transition-colors">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          {note.score !== undefined && (
            <span className="mb-1 inline-block rounded-full bg-accent-primary/10 px-1.5 py-0.5 text-xs text-accent-primary">
              {Math.round(note.score * 100)}% match
            </span>
          )}
          <p className="text-sm text-text-primary leading-snug">{note.text}</p>
          <p className="mt-1 text-xs text-text-muted">{note.date_str} · {note.time_str}</p>
        </div>
        <button
          onClick={() => onDelete(note.id)}
          className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-status-error transition-all"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}

export function NotesPanel() {
  const { notes, searchResults, loading, add, search, remove } = useNotes()
  const [query,      setQuery]      = useState('')
  const [newText,    setNewText]    = useState('')
  const [searched,   setSearched]   = useState(false)
  const [adding,     setAdding]     = useState(false)

  const handleSearch = async () => {
    if (!query.trim()) return
    await search(query.trim())
    setSearched(true)
  }

  const handleAdd = async () => {
    if (!newText.trim()) return
    setAdding(true)
    await add(newText.trim())
    setNewText('')
    setAdding(false)
  }

  const displayed = searched ? searchResults : notes

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4 text-accent-primary" />
        <span className="text-sm font-medium text-text-primary">Voice Notes</span>
        <span className="text-xs text-text-muted">({notes.length})</span>
      </div>

      {/* Add note */}
      <div className="flex gap-2">
        <input
          className="flex-1 rounded-lg bg-surface-secondary border border-surface-border px-3 py-1.5 text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary/50"
          placeholder='e.g. "Client wants report by Friday"'
          value={newText}
          onChange={e => setNewText(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleAdd()}
        />
        <button
          onClick={handleAdd}
          disabled={!newText.trim() || adding}
          className="rounded-lg bg-accent-primary/20 px-3 py-1.5 text-xs font-medium text-accent-primary hover:bg-accent-primary/30 transition-colors disabled:opacity-40"
        >
          {adding ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
        </button>
      </div>

      {/* Search */}
      <div className="flex gap-2">
        <input
          className="flex-1 rounded-lg bg-surface-secondary border border-surface-border px-3 py-1.5 text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary/50"
          placeholder="Search notes semantically…"
          value={query}
          onChange={e => { setQuery(e.target.value); if (!e.target.value) setSearched(false) }}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
        />
        <button
          onClick={handleSearch}
          disabled={!query.trim() || loading}
          className="rounded-lg bg-surface-border/50 px-3 py-1.5 text-text-secondary hover:bg-surface-border transition-colors disabled:opacity-40"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
        </button>
      </div>

      {/* Results */}
      {searched && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-text-muted">{searchResults.length} result{searchResults.length !== 1 ? 's' : ''} for &quot;{query}&quot;</p>
          <button onClick={() => { setSearched(false); setQuery('') }} className="text-xs text-accent-primary hover:underline">
            Clear
          </button>
        </div>
      )}

      {!loading && displayed.length === 0 && (
        <p className="py-4 text-center text-xs text-text-muted">
          {searched ? 'No notes match that search.' : 'No notes yet. Say "note that…" or type above.'}
        </p>
      )}

      <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
        <AnimatePresence>
          {displayed.map(n => (
            <motion.div
              key={n.id}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
            >
              <NoteCard note={n} onDelete={remove} />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
