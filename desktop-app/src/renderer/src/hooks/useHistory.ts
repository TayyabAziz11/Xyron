import { useCallback, useEffect, useState } from 'react'

export interface HistoryEntry {
  id:         number
  ts:         number
  date_str:   string
  time_str:   string
  command:    string
  tool_used:  string | null
  result:     string | null
  session_id: string | null
}

const API_BASE = 'http://localhost:8000'

export function useHistory() {
  const [entries,  setEntries]  = useState<HistoryEntry[]>([])
  const [loading,  setLoading]  = useState(false)
  const [summary,  setSummary]  = useState('')

  const loadToday = useCallback(async () => {
    setLoading(true)
    try {
      const r    = await fetch(`${API_BASE}/api/v1/history/today`)
      const data = await r.json()
      setEntries(data.entries ?? [])
      setSummary(data.spoken_summary ?? '')
    } catch { /* ok */ } finally { setLoading(false) }
  }, [])

  const search = useCallback(async (keyword: string) => {
    setLoading(true)
    try {
      const r    = await fetch(`${API_BASE}/api/v1/history?keyword=${encodeURIComponent(keyword)}`)
      const data = await r.json()
      setEntries(data.entries ?? [])
    } catch { /* ok */ } finally { setLoading(false) }
  }, [])

  const loadDate = useCallback(async (date: string) => {
    setLoading(true)
    try {
      const r    = await fetch(`${API_BASE}/api/v1/history?date=${date}`)
      const data = await r.json()
      setEntries(data.entries ?? [])
    } catch { /* ok */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { loadToday() }, [loadToday])

  return { entries, loading, summary, loadToday, search, loadDate }
}
