import { useCallback, useEffect, useRef, useState } from 'react'
import { takeoverApi, type TakeoverStatus } from '@desktop/services/takeoverApi'

interface UseTakeoverReturn {
  status:     TakeoverStatus | null
  loading:    boolean
  toggling:   boolean
  error:      string | null
  activate:   () => Promise<void>
  deactivate: () => Promise<void>
  toggle:     () => Promise<void>
}

export function useTakeoverStatus(): UseTakeoverReturn {
  const [status,   setStatus]   = useState<TakeoverStatus | null>(null)
  const [loading,  setLoading]  = useState(true)
  const [toggling, setToggling] = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const s = await takeoverApi.status()
      setStatus(s)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'takeover unavailable')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    timer.current = setInterval(fetchStatus, 8000)
    return () => { if (timer.current) clearInterval(timer.current) }
  }, [fetchStatus])

  const activate = useCallback(async () => {
    setToggling(true)
    try {
      const s = await takeoverApi.activate()
      setStatus(s)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'activate failed')
    } finally {
      setToggling(false)
    }
  }, [])

  const deactivate = useCallback(async () => {
    setToggling(true)
    try {
      const s = await takeoverApi.deactivate()
      setStatus(s)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'deactivate failed')
    } finally {
      setToggling(false)
    }
  }, [])

  const toggle = useCallback(async () => {
    if (status?.active) await deactivate()
    else await activate()
  }, [status, activate, deactivate])

  return { status, loading, toggling, error, activate, deactivate, toggle }
}
