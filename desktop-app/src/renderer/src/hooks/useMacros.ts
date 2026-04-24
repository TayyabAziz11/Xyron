import { useCallback, useEffect, useState } from 'react'

export interface MacroStep {
  tool:        string
  params:      Record<string, unknown>
  description: string
}

export interface Macro {
  id:         number
  trigger:    string
  name:       string
  steps:      MacroStep[]
  active:     number
  created_at: number
  run_count:  number
}

const API_BASE = 'http://localhost:8000'

export function useMacros() {
  const [macros,  setMacros]  = useState<Macro[]>([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r    = await fetch(`${API_BASE}/api/v1/macros`)
      const data = await r.json()
      setMacros(data.macros ?? [])
    } catch { /* ok */ } finally { setLoading(false) }
  }, [])

  const create = useCallback(async (trigger: string, name: string, steps: MacroStep[]) => {
    const r    = await fetch(`${API_BASE}/api/v1/macros`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ trigger, name, steps }),
    })
    const data = await r.json()
    setMacros(prev => [data.macro, ...prev])
    return data.macro as Macro
  }, [])

  const remove = useCallback(async (id: number) => {
    await fetch(`${API_BASE}/api/v1/macros/${id}`, { method: 'DELETE' })
    setMacros(prev => prev.filter(m => m.id !== id))
  }, [])

  const toggle = useCallback(async (id: number, active: boolean) => {
    await fetch(`${API_BASE}/api/v1/macros/${id}/toggle`, {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ active }),
    })
    setMacros(prev => prev.map(m => m.id === id ? { ...m, active: active ? 1 : 0 } : m))
  }, [])

  useEffect(() => { load() }, [load])

  return { macros, loading, load, create, remove, toggle }
}
