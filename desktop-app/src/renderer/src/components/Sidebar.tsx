import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Mic, Zap, Clock, Settings, WifiOff, Keyboard } from 'lucide-react'
import xyronIcon from '../assets/xyron-icon.png'
import { useAssistantSettings, MODE_OPTIONS } from '../hooks/useAssistantSettings'

const NAV = [
  { to: '/',         icon: Mic,      label: 'Voice',    shortcut: '1' },
  { to: '/commands', icon: Zap,      label: 'Commands', shortcut: '2' },
  { to: '/activity', icon: Clock,    label: 'Activity', shortcut: '3' },
  { to: '/settings', icon: Settings, label: 'Settings', shortcut: '4' },
]

export default function Sidebar() {
  const [backendOk, setBackendOk]   = useState<boolean | null>(null)
  const [hovered,   setHovered]     = useState<string | null>(null)
  const { settings }                = useAssistantSettings()
  const modeInfo                    = MODE_OPTIONS.find(m => m.id === settings.mode)

  useEffect(() => {
    const check = async () => {
      try {
        const r = await fetch('http://localhost:8000/api/v1/health')
        setBackendOk(r.ok)
      } catch { setBackendOk(false) }
    }
    check()
    const id = setInterval(check, 8000)
    return () => clearInterval(id)
  }, [])

  return (
    <aside className="w-[200px] flex-shrink-0 h-full flex flex-col border-r border-border/50 bg-surface/40 backdrop-blur-xl relative overflow-hidden">
      {/* Top accent glow */}
      <div className="absolute top-0 left-0 right-0 h-32 bg-gradient-to-b from-brand/10 to-transparent pointer-events-none" />
      <div className="absolute inset-0 bg-grid pointer-events-none opacity-40" />

      {/* Logo */}
      <div className="relative flex items-center gap-3 px-4 py-4 border-b border-border/40">
        <div className="relative">
          <motion.img
            src={xyronIcon}
            alt="Xyron"
            animate={{ scale: [1, 1.04, 1] }}
            transition={{ duration: 5, repeat: Infinity, ease: 'easeInOut' }}
            className="w-8 h-8 rounded-xl flex-shrink-0 object-cover"
            style={{ boxShadow: '0 0 16px rgba(124,58,237,0.45), 0 0 32px rgba(124,58,237,0.15)' }}
          />
          {/* Online dot */}
          <div className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-surface ${
            backendOk === null ? 'bg-warn' : backendOk ? 'bg-ok' : 'bg-err'
          }`}>
            {backendOk && (
              <div className="absolute inset-0 rounded-full bg-ok animate-ping opacity-60" />
            )}
          </div>
        </div>
        <div>
          <div className="text-[13px] font-bold gradient-text tracking-widest leading-tight">XYRON</div>
          <div className="text-[9px] text-slate-600 leading-tight mt-0.5">AI Voice Assistant</div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-2.5 space-y-0.5 relative">
        {NAV.map(({ to, icon: Icon, label, shortcut }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            onMouseEnter={() => setHovered(to)}
            onMouseLeave={() => setHovered(null)}
          >
            {({ isActive }) => (
              <motion.div
                className={`nav-item ${isActive ? 'active' : ''}`}
                whileHover={{ x: 2 }}
                whileTap={{ scale: 0.97 }}
                transition={{ duration: 0.12 }}
              >
                <div className={`w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0 transition-all duration-200 ${
                  isActive ? 'bg-brand/20 text-brand-light' : 'text-slate-500'
                }`}>
                  <Icon size={13} />
                </div>
                <span className="flex-1 text-[13px]">{label}</span>

                <AnimatePresence>
                  {hovered === to && !isActive && (
                    <motion.span
                      initial={{ opacity: 0, scale: 0.75 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0, scale: 0.75 }}
                      className="text-[9px] font-mono text-slate-600 bg-border px-1.5 py-0.5 rounded"
                    >
                      {shortcut}
                    </motion.span>
                  )}
                </AnimatePresence>

                {isActive && (
                  <motion.div
                    layoutId="active-dot"
                    className="w-1 h-1 rounded-full bg-brand-light"
                    transition={{ type: 'spring', stiffness: 500, damping: 35 }}
                  />
                )}
              </motion.div>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Current mode chip */}
      <div className="px-3 pb-2">
        <NavLink to="/settings">
          <motion.div
            whileHover={{ scale: 1.02 }}
            className="flex items-center gap-2 px-3 py-2 rounded-xl bg-brand/8 border border-brand/20 cursor-pointer hover:bg-brand/12 hover:border-brand/30 transition-all duration-200"
          >
            <span className="text-base leading-none">{modeInfo?.emoji ?? '🤖'}</span>
            <div className="flex-1 min-w-0">
              <div className="text-[10px] text-slate-600 leading-tight">Active mode</div>
              <div className="text-[11px] font-semibold text-brand-light leading-tight truncate">{modeInfo?.label ?? 'Assistant'}</div>
            </div>
            <Settings size={9} className="text-slate-600 flex-shrink-0" />
          </motion.div>
        </NavLink>
      </div>

      {/* Shortcut hint */}
      <div className="px-3 pb-2">
        <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-card/40 border border-border/30">
          <Keyboard size={9} className="text-slate-700 flex-shrink-0" />
          <span className="text-[9px] text-slate-700">
            <kbd className="font-mono bg-border/80 px-1 rounded text-[8px]">Alt</kbd>
            {' + '}
            <kbd className="font-mono bg-border/80 px-1 rounded text-[8px]">Space</kbd>
            {' toggle'}
          </span>
        </div>
      </div>

      {/* Backend status */}
      <div className="p-3 pt-0 border-t border-border/40">
        <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-card/50 border border-border/30">
          {backendOk === null ? (
            <div className="w-1.5 h-1.5 rounded-full bg-warn animate-pulse flex-shrink-0" />
          ) : backendOk ? (
            <div className="relative flex-shrink-0">
              <div className="w-1.5 h-1.5 rounded-full bg-ok" />
              <div className="absolute inset-0 rounded-full bg-ok animate-ping opacity-60" />
            </div>
          ) : (
            <WifiOff size={10} className="text-err flex-shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            <div className="text-[9px] text-slate-700">Backend API</div>
            <div className={`text-[10px] font-semibold leading-tight ${
              backendOk === null ? 'text-warn' : backendOk ? 'text-ok' : 'text-err'
            }`}>
              {backendOk === null ? 'Connecting…' : backendOk ? 'Online' : 'Offline'}
            </div>
          </div>
        </div>
      </div>
    </aside>
  )
}
