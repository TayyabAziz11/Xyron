

import { useState } from 'react'
import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { useUIMode } from '@/contexts/UIModeContext'
import type { UIMode } from '@/hooks/useUIMode'
import { useCognitiveState } from '@/hooks/useCognitiveState'
import { PassiveHUD } from '@/components/ambient'

const MODE_STYLES: Record<UIMode, { label: string; classes: string }> = {
  default:   { label: 'DEFAULT',   classes: 'bg-white/10 text-white/50 border-white/20' },
  focus:     { label: 'FOCUS',     classes: 'bg-cyan-500/20 text-cyan-300 border-cyan-400/50' },
  calm:      { label: 'CALM',      classes: 'bg-purple-500/20 text-purple-300 border-purple-400/50' },
  overdrive: { label: 'OVERDRIVE', classes: 'bg-red-500/20 text-red-400 border-red-500/60 animate-pulse' },
  sentinel:  { label: 'SENTINEL',  classes: 'bg-red-900/40 text-red-300 border-red-500 animate-pulse' },
}

interface AppShellProps {
  children: React.ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const mode     = useUIMode()
  const cogState = useCognitiveState()
  const { label, classes } = MODE_STYLES[mode]

  return (
    <div data-ui-mode={mode} className="flex min-h-screen bg-surface-base">
      {/* Sidebar — hidden on mobile, slide-in overlay */}
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      {/* Mobile backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/70 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <div className="flex flex-1 flex-col lg:pl-60">
        <PassiveHUD cognitiveState={cogState} />
        <Header onMenuClick={() => setSidebarOpen(true)} />
        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>

      {/* UI mode badge — bottom-right, always visible */}
      <div className="fixed bottom-4 right-4 z-50 flex items-center gap-1.5">
        <div className={`flex items-center gap-1.5 rounded border px-2.5 py-1 font-mono text-[10px] tracking-widest backdrop-blur-sm transition-all duration-500 ${classes}`}>
          <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
          UI: {label}
        </div>
      </div>
    </div>
  )
}
