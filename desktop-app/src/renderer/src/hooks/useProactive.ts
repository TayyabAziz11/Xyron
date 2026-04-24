import { useCallback, useEffect, useRef, useState } from 'react'

export interface ProactiveNotification {
  message:  string
  category: 'meeting' | 'break' | 'reminder' | 'tip' | 'info'
  priority: number
  ts:       number
  id:       number
}

const API_BASE = 'http://localhost:8000'

let _notifId = 0

export function useProactive(onSpeak?: (text: string) => void) {
  const [notifications, setNotifications] = useState<ProactiveNotification[]>([])
  const [connected,     setConnected]     = useState(false)
  const esRef = useRef<EventSource | null>(null)

  const dismiss = useCallback((id: number) => {
    setNotifications(prev => prev.filter(n => n.id !== id))
  }, [])

  const connect = useCallback(() => {
    if (esRef.current) esRef.current.close()
    const es = new EventSource(`${API_BASE}/api/v1/proactive/stream`)
    esRef.current = es

    es.onopen = () => setConnected(true)

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'connected') return
        if (data.type === 'notification') {
          const notif: ProactiveNotification = { ...data, id: ++_notifId }
          setNotifications(prev => [notif, ...prev].slice(0, 10))
          onSpeak?.(data.message)
          if (data.priority >= 6) {
            setTimeout(() => dismiss(notif.id), 15000)
          }
        }
      } catch { /* ok */ }
    }

    es.onerror = () => {
      setConnected(false)
      setTimeout(connect, 10000)
    }
  }, [dismiss, onSpeak])

  useEffect(() => {
    connect()
    return () => { esRef.current?.close() }
  }, [connect])

  return { notifications, connected, dismiss }
}
