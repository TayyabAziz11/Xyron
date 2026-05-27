

import { useCallback, useEffect, useState } from 'react'

export interface Note {
  id:         number
  ts:         number
  date_str:   string
  time_str:   string
  text:       string
  session_id: string | null
  score?:     number
}

const API_BASE = typeof window !== 'undefined' ? '' : 'http://localhost:8000'

export function useNotes() {
  const [notes,         setNotes]         = useState<Note[]>([])
  const [searchResults, setSearchResults] = useState<Note[]>([])
  const [loading,       setLoading]       = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r    = await fetch(`${API_BASE}/api/v1/notes`)
      const data = await r.json()
      setNotes(data.notes ?? [])
    } catch { /* ok */ } finally { setLoading(false) }
  }, [])

  const add = useCallback(async (text: string) => {
    const r    = await fetch(`${API_BASE}/api/v1/notes`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ text }),
    })
    const data = await r.json()
    setNotes(prev => [data.note, ...prev])
    return data.note as Note
  }, [])

  const search = useCallback(async (query: string) => {
    setLoading(true)
    try {
      const r    = await fetch(`${API_BASE}/api/v1/notes/search`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ query }),
      })
      const data = await r.json()
      setSearchResults(data.results ?? [])
      return data.spoken as string
    } catch { return '' } finally { setLoading(false) }
  }, [])

  const remove = useCallback(async (id: number) => {
    await fetch(`${API_BASE}/api/v1/notes/${id}`, { method: 'DELETE' })
    setNotes(prev => prev.filter(n => n.id !== id))
  }, [])

  useEffect(() => { load() }, [load])

  return { notes, searchResults, loading, load, add, search, remove }
}
