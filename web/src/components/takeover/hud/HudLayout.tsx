'use client'

import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import LeftPanel      from './LeftPanel'
import RightPanel     from './RightPanel'
import TerminalFeed   from './TerminalFeed'
import NeuralBrain    from './NeuralBrain'
import DirectivePanel from './DirectivePanel'
import type { TakeoverPhase } from '../../../hooks/useTakeoverMode'

interface Props {
  phase:         TakeoverPhase
  systemLine:    string
  showDirective: boolean
  onClose:       () => void
}

export default function HudLayout({ phase, systemLine, showDirective, onClose }: Props) {
  const active = phase === 'hud' || phase === 'active'

  return (
    <>
      {/* Full-screen dark backdrop */}
      <AnimatePresence>
        {active && (
          <motion.div
            key="hud-bg"
            className="fixed inset-0 z-[65] pointer-events-none"
            style={{ background: 'rgba(0,0,0,0.82)', backdropFilter: 'blur(14px) saturate(1.4)' }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.5 }}
          />
        )}
      </AnimatePresence>

      {/* Close button */}
      <AnimatePresence>
        {active && (
          <motion.button key="close" onClick={onClose}
            className="fixed top-4 right-4 z-[80] p-2 rounded-lg transition-all"
            style={{ color: 'rgba(220,80,80,0.7)', background: 'rgba(200,0,0,0.1)', border: '1px solid rgba(200,0,0,0.25)' }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ delay: 1 }}
            onMouseEnter={e => (e.currentTarget.style.color = 'rgba(255,100,100,1)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'rgba(220,80,80,0.7)')}
          >
            <X size={14} />
          </motion.button>
        )}
      </AnimatePresence>

      {/* Top bar */}
      <AnimatePresence>
        {active && (
          <motion.div key="topbar"
            className="fixed top-0 left-0 right-0 z-[76] flex items-center justify-between px-6 py-3"
            style={{
              borderBottom: '1px solid rgba(200,0,0,0.20)',
              background: 'rgba(6,0,0,0.6)',
              backdropFilter: 'blur(8px)',
            }}
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ delay: 0.1, duration: 0.4 }}
          >
            <div className="flex items-center gap-3">
              <motion.div className="w-2 h-2 rounded-full"
                style={{ background: 'rgba(255,50,50,0.95)', boxShadow: '0 0 8px rgba(255,0,0,0.9)' }}
                animate={{ opacity: [1, 0.3, 1] }}
                transition={{ duration: 1.2, repeat: Infinity }}
              />
              <span className="text-[9px] font-mono tracking-[6px] uppercase" style={{ color: 'rgba(220,80,80,0.85)' }}>
                XYRON AUTONOMOUS COMMAND CENTER
              </span>
            </div>
            <span className="text-[9px] font-mono tracking-[4px]" style={{ color: 'rgba(255,60,60,0.75)' }}>
              TAKEOVER MODE ACTIVE
            </span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Center neural brain + headline */}
      <AnimatePresence>
        {active && (
          <motion.div key="center"
            className="fixed z-[67] flex flex-col items-center"
            style={{
              top: '50%', left: '50%',
              transform: 'translate(-50%, -50%)',
              width: 460, height: 420,
            }}
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={{ delay: 0.2, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          >
            {/* Headline above brain */}
            <div className="text-center mb-3 pointer-events-none select-none">
              <div className="text-[8px] font-mono tracking-[8px] uppercase mb-1.5" style={{ color: 'rgba(220,60,60,0.6)' }}>
                NEURAL CORE ONLINE
              </div>
              <div className="text-[20px] font-black tracking-tight" style={{
                color: 'rgba(255,90,90,0.98)',
                fontFamily: 'monospace',
                textShadow: '0 0 24px rgba(255,0,0,0.5), 0 0 60px rgba(200,0,0,0.25)',
              }}>
                {systemLine}
              </div>
            </div>

            {/* Neural brain canvas */}
            <div className="flex-1 w-full rounded-xl overflow-hidden"
              style={{
                border: '1px solid rgba(200,0,0,0.22)',
                background: 'rgba(4,0,0,0.6)',
                boxShadow: '0 0 40px rgba(180,0,0,0.1) inset',
              }}>
              <NeuralBrain />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Side panels */}
      <LeftPanel  active={active} />
      <RightPanel active={active} />

      {/* Terminal feed at bottom */}
      <TerminalFeed active={active} />

      {/* Directive panel — appears after workspace launch */}
      <DirectivePanel visible={active && showDirective} />

      {/* Bottom status bar */}
      <AnimatePresence>
        {active && (
          <motion.div key="bottombar"
            className="fixed bottom-0 left-0 right-0 z-[76] flex items-center justify-between px-6 py-2"
            style={{
              borderTop: '1px solid rgba(200,0,0,0.18)',
              background: 'rgba(6,0,0,0.6)',
              backdropFilter: 'blur(8px)',
            }}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 20 }}
            transition={{ delay: 0.5, duration: 0.4 }}
          >
            <motion.span className="text-[8px] font-mono tracking-widest uppercase"
              style={{ color: 'rgba(200,60,60,0.6)' }}
              animate={{ opacity: [0.6, 1, 0.6] }}
              transition={{ duration: 3.5, repeat: Infinity }}>
              WORKSPACE SEIZED — XYRON IN CONTROL
            </motion.span>
            <button onClick={onClose}
              className="text-[8px] font-mono tracking-widest uppercase transition-colors"
              style={{ color: 'rgba(180,60,60,0.55)' }}
              onMouseEnter={e => (e.currentTarget.style.color = 'rgba(255,80,80,0.9)')}
              onMouseLeave={e => (e.currentTarget.style.color = 'rgba(180,60,60,0.55)')}>
              ESC TO EXIT
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Scanning sweep line */}
      <AnimatePresence>
        {active && (
          <motion.div key="sweep" className="fixed left-0 right-0 z-[77] h-px pointer-events-none"
            style={{ background: 'linear-gradient(90deg, transparent, rgba(255,30,30,0.45), transparent)' }}
            animate={{ top: ['0%', '100%'] }}
            transition={{ duration: 7, repeat: Infinity, ease: 'linear', delay: 0.5 }}
          />
        )}
      </AnimatePresence>
    </>
  )
}
