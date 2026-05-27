import { useEffect, useState, useCallback } from 'react'
import { invoke } from '@tauri-apps/api/core'

interface BackendStatus { running: boolean }

export function useBackendManager() {
  const [status, setStatus] = useState<BackendStatus>({ running: false })

  const check = useCallback(async () => {
    try {
      const s = await invoke<BackendStatus>('get_backend_status')
      setStatus(s)
    } catch {
      setStatus({ running: false })
    }
  }, [])

  const start = useCallback(async () => {
    try {
      await invoke('start_backend')
      setTimeout(check, 2000)
    } catch (e) {
      console.error('[BackendManager] start failed:', e)
    }
  }, [check])

  useEffect(() => {
    check()
    const id = setInterval(check, 15_000)
    return () => clearInterval(id)
  }, [check])

  return { status, start, check }
}
