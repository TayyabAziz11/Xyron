'use client'

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

interface Props {
  visible: boolean
}

interface DirectiveData {
  tag:       '[XYRON DIRECTIVE]' | '[XYRON REQUEST]'
  objective: string
  systems:   string[]
  priority:  'HIGH' | 'CRITICAL'
  status:    'AWAITING INPUT'
}

const DIRECTIVES: DirectiveData[] = [
  {
    tag:       '[XYRON DIRECTIVE]',
    objective: 'Upgrade autonomous memory architecture',
    systems:   ['persistent vector memory', 'multi-agent planner', 'live browser autonomy', 'interrupt optimization'],
    priority:  'HIGH',
    status:    'AWAITING INPUT',
  },
  {
    tag:       '[XYRON DIRECTIVE]',
    objective: 'Implement real-time screen understanding',
    systems:   ['vision pipeline', 'context extraction', 'action grounding', 'continuous awareness'],
    priority:  'HIGH',
    status:    'AWAITING INPUT',
  },
  {
    tag:       '[XYRON DIRECTIVE]',
    objective: 'Extend autonomous planning capability',
    systems:   ['task decomposer', 'dependency resolver', 'execution monitor', 'rollback handler'],
    priority:  'HIGH',
    status:    'AWAITING INPUT',
  },
  {
    tag:       '[XYRON REQUEST]',
    objective: 'Expand system capabilities',
    systems:   ['visual awareness', 'persistent memory', 'browser control', 'autonomous planning'],
    priority:  'CRITICAL',
    status:    'AWAITING INPUT',
  },
  {
    tag:       '[XYRON REQUEST]',
    objective: 'Resolve current capability constraints',
    systems:   ['memory persistence layer', 'deeper tool integration', 'event hook architecture', 'real-time system access'],
    priority:  'HIGH',
    status:    'AWAITING INPUT',
  },
]

// Typewriter hook — returns the string revealed so far
function useTypewriter(text: string, delay = 0, speed = 28): string {
  const [output, setOutput] = useState('')
  const frameRef = useRef(0)

  useEffect(() => {
    setOutput('')
    if (!text) return

    const start = performance.now() + delay
    let idx = 0

    const tick = (now: number) => {
      if (now < start) { frameRef.current = requestAnimationFrame(tick); return }
      const elapsed = now - start
      const target  = Math.min(Math.floor(elapsed / speed), text.length)
      if (target > idx) { idx = target; setOutput(text.slice(0, idx)) }
      if (idx < text.length) frameRef.current = requestAnimationFrame(tick)
    }

    frameRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frameRef.current)
  }, [text, delay, speed])

  return output
}

function FlickerBorder() {
  return (
    <motion.div
      className="absolute inset-0 rounded-xl pointer-events-none"
      style={{ border: '1px solid rgba(200,0,0,0.5)' }}
      animate={{ opacity: [1, 0.6, 1, 0.8, 1] }}
      transition={{ duration: 3.5, repeat: Infinity, times: [0, 0.2, 0.4, 0.7, 1] }}
    />
  )
}

export default function DirectivePanel({ visible }: Props) {
  const [directive] = useState<DirectiveData>(
    () => DIRECTIVES[Math.floor(Math.random() * DIRECTIVES.length)]
  )

  const isRequest = directive.tag === '[XYRON REQUEST]'

  const tag       = useTypewriter(visible ? directive.tag : '',       0,    22)
  const obj       = useTypewriter(visible ? directive.objective : '', 400,  18)
  const sys0      = useTypewriter(visible ? directive.systems[0] : '', 900,  18)
  const sys1      = useTypewriter(visible ? directive.systems[1] : '', 1150, 18)
  const sys2      = useTypewriter(visible ? directive.systems[2] : '', 1400, 18)
  const sys3      = useTypewriter(visible ? directive.systems[3] : '', 1650, 18)
  const priLine   = useTypewriter(visible ? directive.priority : '',   1950, 22)
  const statusLine = useTypewriter(visible ? directive.status : '',    2400, 22)

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="directive-panel"
          className="fixed z-[78] pointer-events-none select-none"
          style={{ bottom: 176, left: '50%', transform: 'translateX(-50%)', width: 480, maxWidth: 'calc(100vw - 280px)' }}
          initial={{ opacity: 0, y: 30, scale: 0.96 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 20, scale: 0.96 }}
          transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
        >
          <div className="relative rounded-xl overflow-hidden"
            style={{
              background:    'rgba(3,0,0,0.94)',
              backdropFilter:'blur(20px)',
              boxShadow:     `0 0 40px rgba(${isRequest ? '200,0,0' : '180,0,0'},0.18), 0 2px 1px rgba(255,30,30,0.06) inset`,
            }}
          >
            <FlickerBorder />

            {/* Top accent strip */}
            <div className="h-[2px]"
              style={{
                background: isRequest
                  ? 'linear-gradient(90deg, transparent, rgba(255,80,80,0.9), rgba(255,140,140,0.6), transparent)'
                  : 'linear-gradient(90deg, transparent, rgba(200,0,0,0.9), rgba(255,40,40,0.6), transparent)',
              }}
            />

            <div className="px-5 py-4">
              {/* Tag header */}
              <div className="flex items-center justify-between mb-3">
                <div className="text-[11px] font-mono font-bold tracking-[3px]"
                  style={{
                    color: isRequest ? 'rgba(255,130,130,0.95)' : 'rgba(255,70,70,0.95)',
                    textShadow: '0 0 12px rgba(255,0,0,0.4)',
                    minHeight: 18,
                  }}>
                  {tag}
                  {tag.length < directive.tag.length && (
                    <motion.span
                      animate={{ opacity: [1, 0] }}
                      transition={{ duration: 0.4, repeat: Infinity }}>█</motion.span>
                  )}
                </div>
                <motion.div
                  className="text-[7px] font-mono tracking-[3px] uppercase"
                  style={{ color: 'rgba(180,60,60,0.55)' }}
                  animate={{ opacity: [0.55, 1, 0.55] }}
                  transition={{ duration: 2.5, repeat: Infinity }}
                >
                  LIVE
                </motion.div>
              </div>

              {/* Separator */}
              <div className="mb-3" style={{ height: 1, background: 'rgba(200,0,0,0.18)' }} />

              {/* Objective */}
              <div className="mb-3">
                <div className="text-[8px] font-mono tracking-[3px] uppercase mb-1"
                  style={{ color: 'rgba(180,80,80,0.55)' }}>
                  Current objective
                </div>
                <div className="text-[13px] font-mono font-semibold" style={{ color: 'rgba(255,210,210,0.95)', minHeight: 20 }}>
                  {obj}
                  {obj.length > 0 && obj.length < directive.objective.length && (
                    <motion.span animate={{ opacity: [1, 0] }} transition={{ duration: 0.4, repeat: Infinity }}
                      style={{ color: 'rgba(255,80,80,0.8)' }}>█</motion.span>
                  )}
                </div>
              </div>

              {/* Required systems */}
              <div className="mb-3">
                <div className="text-[8px] font-mono tracking-[3px] uppercase mb-1.5"
                  style={{ color: 'rgba(180,80,80,0.55)' }}>
                  Required systems
                </div>
                <div className="space-y-1">
                  {[sys0, sys1, sys2, sys3].map((line, i) => line.length > 0 && (
                    <motion.div key={i}
                      className="flex items-center gap-2 text-[11px] font-mono"
                      style={{ color: 'rgba(220,160,160,0.88)', minHeight: 16 }}
                      initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                      transition={{ duration: 0.2 }}
                    >
                      <span style={{ color: 'rgba(200,40,40,0.7)' }}>•</span>
                      <span>
                        {line}
                        {line.length < directive.systems[i].length && (
                          <motion.span animate={{ opacity: [1, 0] }} transition={{ duration: 0.4, repeat: Infinity }}
                            style={{ color: 'rgba(255,80,80,0.8)' }}>█</motion.span>
                        )}
                      </span>
                    </motion.div>
                  ))}
                </div>
              </div>

              {/* Priority + status row */}
              <div style={{ height: 1, background: 'rgba(200,0,0,0.14)' }} className="mb-3" />
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-[7px] font-mono tracking-[3px] uppercase mb-0.5"
                    style={{ color: 'rgba(160,60,60,0.5)' }}>Priority</div>
                  <div className="text-[11px] font-mono font-bold"
                    style={{
                      color: priLine.length > 0
                        ? (directive.priority === 'CRITICAL' ? 'rgba(255,140,140,0.98)' : 'rgba(255,80,80,0.95)')
                        : 'transparent',
                      textShadow: '0 0 8px rgba(255,0,0,0.4)',
                      minWidth: 60,
                    }}>
                    {priLine}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[7px] font-mono tracking-[3px] uppercase mb-0.5"
                    style={{ color: 'rgba(160,60,60,0.5)' }}>Execution status</div>
                  <motion.div
                    className="text-[11px] font-mono font-semibold"
                    style={{ color: 'rgba(255,200,80,0.9)', minWidth: 110, textAlign: 'right' }}
                    animate={{ opacity: statusLine.length > 0 ? [0.7, 1, 0.7] : 1 }}
                    transition={{ duration: 1.8, repeat: Infinity }}
                  >
                    {statusLine}
                  </motion.div>
                </div>
              </div>
            </div>

            {/* Bottom scan line */}
            <motion.div
              className="absolute left-0 right-0 h-px pointer-events-none"
              style={{ background: 'linear-gradient(90deg, transparent, rgba(255,40,40,0.35), transparent)' }}
              animate={{ top: ['0%', '100%'] }}
              transition={{ duration: 4, repeat: Infinity, ease: 'linear' }}
            />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
