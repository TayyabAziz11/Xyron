'use client'

import { useState, useEffect, useCallback, useRef } from 'react'

interface UseApiOptions {
  interval?: number   // polling interval in ms (undefined = no polling)
  enabled?: boolean   // set to false to skip initial fetch
}

interface UseApiResult<T> {
  data: T | null
  loading: boolean
  error: Error | null
  refetch: () => void
}

export function useApi<T>(
  fetchFn: () => Promise<T>,
  options: UseApiOptions = {},
): UseApiResult<T> {
  const { interval, enabled = true } = options
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState<boolean>(enabled)
  const [error, setError] = useState<Error | null>(null)
  const fetchFnRef = useRef(fetchFn)
  fetchFnRef.current = fetchFn

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchFnRef.current()
      setData(result)
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    fetch_()
    if (interval && interval > 0) {
      const id = setInterval(fetch_, interval)
      return () => clearInterval(id)
    }
  }, [enabled, fetch_, interval])

  return { data, loading, error, refetch: fetch_ }
}
