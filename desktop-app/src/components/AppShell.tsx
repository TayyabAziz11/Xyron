import { useState } from 'react'
import { Sidebar } from '@/components/layout/Sidebar'
import { Header } from '@/components/layout/Header'
import { useUIMode } from '@/contexts/UIModeContext'
import { useCognitiveState } from '@/hooks/useCognitiveState'
import PassiveHUD from '@/components/ambient/PassiveHUD'
import { BackendStatus } from './BackendStatus'

const MODE_STYLES: Record<string, { label: string; classes: string }> = {
  default:   { label: 'DEFAULT',   classes: 'bg-white/10 text-white/50 border-white/20' },
  focus:     { label: 'FOCUS',     classes: 'bg-cyan-500/20 text-cyan-300 border-cyan-400/50' },
  calm:      { label: 'CALM',      classes: 'bg-purple-500/20 text-purple-300 border-purple-400/50' },
  overdrive: { label: 'OVERDRIVE', classes: 'bg-red-500/20 text-red-400 border-red-500/60 animate-pulse' },
  sentinel:  { label: 'SENTINEL',  classes: 'bg-red-900/40 text-red-300 border-red-500 animate-pulse' },
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const cogState = useCognitiveState()
  const mode     = useUIMode()
  const modeStyle = MODE_STYLES[mode] ?? MODE_STYLES.default

  return (
    <div data-ui-mode={mode} className="flex h-screen w-screen overflow-hidden bg-[#08080f]">
      <BackendStatus />
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/70 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <div className="flex flex-1 flex-col lg:pl-60 overflow-hidden">
        <PassiveHUD cognitiveState={cogState} />
        <Header onMenuClick={() => setSidebarOpen(true)} />
        <main className="flex-1 overflow-y-auto overflow-x-hidden">{children}</main>
      </div>

      {/* UI mode badge */}
      <div className="fixed bottom-4 right-4 z-50">
        <div className={`flex items-center gap-1.5 rounded border px-2.5 py-1 font-mono text-[10px] tracking-widest backdrop-blur-sm transition-all duration-500 ${modeStyle.classes}`}>
          <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
          UI: {modeStyle.label}
        </div>
      </div>
    </div>
  )
}
