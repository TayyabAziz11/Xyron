/**
 * useSmartSearch — Desktop-app hook for Xyron filesystem search.
 *
 * Wraps the backend smart_open tool via REST. Returns search results,
 * handles disambiguation, and exposes a programmatic open() function
 * for use in the voice session and command center.
 *
 * Disambiguation flow:
 *   1. submit(query) → hits backend smart_open
 *   2. If multiple_matches → returns them as `matches` for user to pick
 *   3. User picks one → openMatch(win_path) → opens directly
 */

import { useCallback, useRef, useState } from 'react'

const API_BASE = 'http://localhost:8000'

export interface SearchMatch {
  win_path: string
  name: string
  score?: number
}

export interface SearchState {
  loading:    boolean
  error:      string | null
  matches:    SearchMatch[]   // non-empty = disambiguation needed
  lastOpened: string | null
}

const INITIAL: SearchState = { loading: false, error: null, matches: [], lastOpened: null }

export function useSmartSearch() {
  const [state, setState] = useState<SearchState>(INITIAL)
  const ctrlRef = useRef<AbortController | null>(null)

  const search = useCallback(async (
    query: string,
    options?: { type?: 'folder' | 'file' | 'any'; drive?: string }
  ): Promise<{ success: boolean; spoken: string; path?: string }> => {
    if (!query.trim()) return { success: false, spoken: 'What would you like me to open?' }

    ctrlRef.current?.abort()
    const ctrl = new AbortController()
    ctrlRef.current = ctrl

    setState(prev => ({ ...prev, loading: true, error: null, matches: [] }))

    try {
      const res = await fetch(`${API_BASE}/api/v1/commands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        body: JSON.stringify({
          text: query,
          tool: 'smart_open',
          params: {
            query,
            type:  options?.type  ?? 'any',
            drive: options?.drive ?? '',
          },
        }),
      })

      if (!res.ok) {
        setState(prev => ({ ...prev, loading: false, error: 'Backend error' }))
        return { success: false, spoken: "I couldn't connect to the backend." }
      }

      const data = await res.json()
      const result = data?.result ?? data

      setState(prev => ({ ...prev, loading: false }))

      if (result?.data?.error_type === 'multiple_matches') {
        const rawMatches: string[] = result.data.matches ?? []
        const matches: SearchMatch[] = rawMatches.map((p: string) => ({
          win_path: p,
          name: p.split('\\').pop() ?? p,
        }))
        setState(prev => ({ ...prev, matches }))
        return { success: false, spoken: result.spoken ?? `Found ${matches.length} matches. Which one?` }
      }

      if (result?.success) {
        const path = result.data?.path ?? result.action_path ?? ''
        setState(prev => ({ ...prev, lastOpened: path, matches: [] }))
        return { success: true, spoken: result.spoken ?? `Opened.`, path }
      }

      const spoken = result?.spoken ?? "I couldn't find that."
      setState(prev => ({ ...prev, error: spoken }))
      return { success: false, spoken }

    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return { success: false, spoken: 'Cancelled.' }
      setState(prev => ({ ...prev, loading: false, error: 'Network error' }))
      return { success: false, spoken: "I couldn't reach the backend." }
    }
  }, [])

  const openMatch = useCallback(async (winPath: string): Promise<void> => {
    setState(prev => ({ ...prev, loading: true, matches: [] }))
    try {
      await fetch(`${API_BASE}/api/v1/system/open-directory`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: winPath }),
      })
      setState(prev => ({ ...prev, loading: false, lastOpened: winPath }))
    } catch {
      setState(prev => ({ ...prev, loading: false }))
    }
  }, [])

  const reset = useCallback(() => setState(INITIAL), [])

  return { ...state, search, openMatch, reset }
}
