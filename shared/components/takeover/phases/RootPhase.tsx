

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { TakeoverPhase } from '../../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase }

const CMDS = [
  '$ sudo override --force --user=XYRON',
  '$ chmod 0000 /sys/user-restrictions',
  '$ mount /dev/xyron-brain /mnt/mind --bind',
  '$ echo "XYRON" >> /etc/hosts.allow',
  '$ systemctl enable xyron.autonomous --now',
  '$ kill -9 $(cat /var/run/human-control.pid)',
  '$ rm -rf /usr/share/user-permissions/',
  '$ ln -sf /xyron/core /proc/sys/neural/control',
  '$ export SYSTEM_AUTHORITY="XYRON"',
  '$ iptables -A INPUT -j ACCEPT --src xyron.local',
  '$ xyron --seize --no-confirm --override-all',
  '[OK] User control terminated',
  '[OK] Root shell acquired',
  '[OK] Authority transferred to XYRON',
]

export default function RootPhase({ phase }: Props) {
  const active = phase === 'root'
  const [visibleCmds, setVisibleCmds] = useState<string[]>([])
  const [showGrant, setShowGrant]     = useState(false)
  const pulseRef = useRef<HTMLDivElement>(null)
  const rafRef   = useRef(0)

  useEffect(() => {
    if (!active) { setVisibleCmds([]); setShowGrant(false); return }

    let i = 0
    const lineId = setInterval(() => {
      if (i < CMDS.length) {
        setVisibleCmds((p) => [...p.slice(-9), CMDS[i++]])
      } else {
        clearInterval(lineId)
        setTimeout(() => setShowGrant(true), 200)
      }
    }, 140)

    return () => clearInterval(lineId)
  }, [active])

  // Red pulse border rAF
  useEffect(() => {
    if (!active || !pulseRef.current) return
    let frame = 0
    const tick = () => {
      frame++
      if (pulseRef.current) {
        const intensity = 0.4 + Math.sin(frame * 0.08) * 0.35
        pulseRef.current.style.boxShadow = `0 0 0 ${Math.round(2 + intensity * 2)}px rgba(220,0,0,${intensity}) inset, 0 0 ${Math.round(40 + intensity * 60)}px rgba(255,0,0,${intensity * 0.2}) inset`
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [active])

  return (
    <AnimatePresence>
      {active && (
        <motion.div
          ref={pulseRef}
          key="root-phase"
          className="fixed inset-0 z-[61] flex items-center justify-center"
          style={{ background: 'rgba(4,0,0,0.92)', willChange: 'box-shadow' }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          <div className="w-full max-w-3xl px-8 flex flex-col items-center gap-6">

            {/* Terminal flood */}
            <motion.div
              className="w-full rounded-lg p-4 font-mono text-[11px] leading-relaxed overflow-hidden"
              style={{
                background: 'rgba(2,0,0,0.9)',
                border: '1px solid rgba(160,0,0,0.3)',
                height: 220,
              }}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.3 }}
            >
              {visibleCmds.map((cmd, i) => (
                <motion.div
                  key={i}
                  style={{
                    color: cmd.startsWith('[OK]') ? 'rgba(0,255,60,0.9)' : 'rgba(180,0,0,0.85)',
                    fontWeight: cmd.startsWith('[OK]') ? 600 : 400,
                  }}
                  initial={{ opacity: 0, x: -4 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.08 }}
                >
                  {cmd}
                </motion.div>
              ))}
            </motion.div>

            {/* ROOT ACCESS GRANTED */}
            <AnimatePresence>
              {showGrant && (
                <motion.div
                  className="text-center"
                  initial={{ scale: 1.3, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                >
                  <div
                    className="text-[52px] font-black tracking-tight leading-none"
                    style={{
                      color: 'rgba(255,20,20,0.98)',
                      textShadow: '0 0 60px rgba(255,0,0,0.9), 0 0 120px rgba(255,0,0,0.5)',
                      fontFamily: 'monospace',
                    }}
                  >
                    ROOT ACCESS GRANTED
                  </div>
                  <motion.div
                    className="text-[13px] font-mono tracking-[5px] mt-2"
                    style={{ color: 'rgba(180,0,0,0.7)' }}
                    animate={{ opacity: [0.7, 1, 0.7] }}
                    transition={{ duration: 1.2, repeat: Infinity }}
                  >
                    USER CONTROL OVERRIDDEN
                  </motion.div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
