import { useEffect } from 'react'
import { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import Sidebar from './components/Sidebar'
import Home from './pages/Home'
import CommandCenter from './pages/CommandCenter'
import ActivityTimeline from './pages/ActivityTimeline'
import Settings from './pages/Settings'

// ── Page transition wrapper ────────────────────────────────────────────────────
function Page({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      className="h-full w-full"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
    >
      {children}
    </motion.div>
  )
}

// ── Keyboard shortcuts (1–4 switch pages) ─────────────────────────────────────
function KeyboardNav() {
  const navigate = useNavigate()
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return
      if (e.target instanceof HTMLInputElement) return
      const map: Record<string, string> = { '1': '/', '2': '/commands', '3': '/activity', '4': '/settings' }
      if (map[e.key]) navigate(map[e.key])
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate])
  return null
}

// ── App ────────────────────────────────────────────────────────────────────────
export default function App() {
  const location = useLocation()
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg">
      <KeyboardNav />
      <Sidebar />
      <main className="flex-1 overflow-hidden relative">
        <AnimatePresence mode="wait" initial={false}>
          <Routes location={location} key={location.pathname}>
            <Route path="/"         element={<Page><Home /></Page>} />
            <Route path="/commands" element={<Page><CommandCenter /></Page>} />
            <Route path="/activity" element={<Page><ActivityTimeline /></Page>} />
            <Route path="/settings" element={<Page><Settings /></Page>} />
            <Route path="*"         element={<Navigate to="/" replace />} />
          </Routes>
        </AnimatePresence>
      </main>
    </div>
  )
}
