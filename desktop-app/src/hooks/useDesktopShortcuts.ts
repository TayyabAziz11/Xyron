import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export function useDesktopShortcuts() {
  const navigate = useNavigate()

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      // Ctrl+1-9 or Alt+1-9 → navigate
      if ((e.ctrlKey || e.altKey) && !e.metaKey) {
        const routes = [
          '/dashboard', '/commands', '/activity', '/approvals',
          '/history', '/integrations', '/workflows', '/stats', '/settings',
        ]
        const idx = parseInt(e.key) - 1
        if (idx >= 0 && idx < routes.length) {
          e.preventDefault()
          navigate(routes[idx])
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate])
}
