import { motion } from 'framer-motion'
import { useBackendManager } from '../hooks/useBackendManager'
import { Power } from 'lucide-react'

export function BackendStatus() {
  const { status, start } = useBackendManager()

  if (status.running) return null

  return (
    <motion.div
      initial={{ opacity: 0, y: -20 }}
      animate={{ opacity: 1, y: 0 }}
      className="fixed top-10 left-1/2 -translate-x-1/2 z-[100] flex items-center gap-3 px-4 py-2.5 rounded-lg border border-[rgba(245,158,11,0.4)] bg-[rgba(245,158,11,0.08)] backdrop-blur-sm"
    >
      <div className="h-2 w-2 rounded-full bg-[#f59e0b] animate-pulse" />
      <span className="font-mono text-xs text-[#f59e0b]">Backend offline</span>
      <button
        onClick={start}
        className="flex items-center gap-1 px-2 py-0.5 rounded border border-[rgba(245,158,11,0.4)] text-[#f59e0b] hover:bg-[rgba(245,158,11,0.15)] transition-colors font-mono text-[10px]"
      >
        <Power className="w-3 h-3" />
        START
      </button>
    </motion.div>
  )
}
