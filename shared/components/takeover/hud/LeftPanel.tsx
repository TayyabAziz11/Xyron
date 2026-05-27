

import { motion, AnimatePresence } from 'framer-motion'

interface Props { active: boolean }

const MODULES = [
  { name: 'NEURAL ENGINE',   state: 'ONLINE',  accent: true },
  { name: 'VOICE SYSTEM',    state: 'ACTIVE',  accent: true },
  { name: 'MEMORY GRAPH',    state: 'ONLINE',  accent: false },
  { name: 'ACTION EXECUTOR', state: 'READY',   accent: false },
  { name: 'LEARNING MODULE', state: 'ACTIVE',  accent: false },
  { name: 'VISION SYSTEM',   state: 'ONLINE',  accent: false },
  { name: 'CORE MODULES',    state: 'ONLINE',  accent: false },
]

const STATE_COLOR: Record<string, string> = {
  ONLINE: 'rgba(0,255,110,0.92)',
  ACTIVE: 'rgba(0,220,255,0.92)',
  READY:  'rgba(240,210,0,0.92)',
}

export default function LeftPanel({ active }: Props) {
  return (
    <AnimatePresence>
      {active && (
        <motion.div
          key="left-panel"
          className="fixed left-5 top-1/2 -translate-y-1/2 z-[76]"
          style={{ width: 228 }}
          initial={{ opacity: 0, x: -50 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -50 }}
          transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1], delay: 0.1 }}
        >
          <div
            style={{
              background: 'rgba(4,0,0,0.88)',
              border: '1px solid rgba(200,0,0,0.30)',
              borderRadius: 12,
              padding: '14px 16px',
              backdropFilter: 'blur(18px)',
              boxShadow: '0 0 40px rgba(180,0,0,0.10), 0 0 1px rgba(255,40,40,0.15) inset',
            }}
          >
            <div className="text-[8px] font-mono tracking-[4px] uppercase mb-3 pb-2 border-b"
              style={{ color: 'rgba(220,60,60,0.75)', borderColor: 'rgba(200,0,0,0.20)' }}>
              CORE MODULES
            </div>

            <div className="space-y-[1px]">
              {MODULES.map((m, i) => (
                <motion.div
                  key={m.name}
                  className="flex items-center justify-between py-[7px] border-b"
                  style={{ borderColor: 'rgba(180,0,0,0.10)' }}
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.2 + i * 0.07, duration: 0.3 }}
                >
                  <span className="text-[10px] font-mono" style={{ color: 'rgba(220,170,170,0.88)' }}>
                    {m.name}
                  </span>
                  <div className="flex items-center gap-1.5">
                    <motion.div
                      className="w-[6px] h-[6px] rounded-full"
                      style={{ background: STATE_COLOR[m.state], boxShadow: `0 0 8px ${STATE_COLOR[m.state]}` }}
                      animate={{ opacity: [1, 0.45, 1] }}
                      transition={{ duration: 2 + i * 0.3, repeat: Infinity }}
                    />
                    <span className="text-[9px] font-mono tracking-wider font-semibold"
                      style={{ color: STATE_COLOR[m.state] }}>
                      {m.state}
                    </span>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
