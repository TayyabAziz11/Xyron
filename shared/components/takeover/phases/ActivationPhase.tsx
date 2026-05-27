

import { motion, AnimatePresence } from 'framer-motion'
import { useParallaxCore } from '../../../hooks/useParallaxCore'
import type { TakeoverPhase } from '../../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase; systemLine: string }

const ORB_ANGLES = [0, 60, 120, 180, 240, 300]

export default function ActivationPhase({ phase, systemLine }: Props) {
  const active      = phase === 'activation'
  const parallaxRef = useParallaxCore(10)

  return (
    <AnimatePresence>
      {active && (
        <motion.div
          key="activation-phase"
          className="fixed inset-0 z-[61] flex flex-col items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.88)' }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.4 }}
        >
          {/* Energy pulse rings */}
          {[1, 2, 3].map((i) => (
            <motion.div key={i} className="absolute rounded-full pointer-events-none"
              style={{
                border: `1px solid rgba(0,200,255,${0.4 - i * 0.1})`,
                width: i * 220, height: i * 220,
                left: '50%', top: '50%',
                transform: 'translate(-50%, -50%)',
              }}
              animate={{ scale: [1, 1.6, 1], opacity: [0.4, 0, 0.4] }}
              transition={{ duration: 2.5, delay: i * 0.5, repeat: Infinity, ease: 'easeOut' }}
            />
          ))}

          {/* Neural core with parallax */}
          <div ref={parallaxRef} className="relative flex items-center justify-center"
            style={{ width: 320, height: 320, willChange: 'transform' }}>

            {/* Rotating rings */}
            <motion.div className="absolute rounded-full"
              style={{ width: 260, height: 260, border: '1px solid rgba(0,200,255,0.10)', borderTopColor: 'rgba(0,220,255,0.8)' }}
              animate={{ rotate: 360 }}
              transition={{ duration: 8, repeat: Infinity, ease: 'linear' }}
            />
            <motion.div className="absolute rounded-full"
              style={{ width: 210, height: 126, border: '1px solid rgba(0,200,255,0.08)', borderLeftColor: 'rgba(0,220,255,0.65)' }}
              animate={{ rotate: -360 }}
              transition={{ duration: 6, repeat: Infinity, ease: 'linear' }}
            />
            <div className="absolute" style={{ transform: 'rotate(48deg)' }}>
              <motion.div className="rounded-full"
                style={{ width: 175, height: 105, border: '1px solid rgba(0,200,255,0.07)', borderTopColor: 'rgba(0,200,255,0.5)' }}
                animate={{ rotate: 360 }}
                transition={{ duration: 4.5, repeat: Infinity, ease: 'linear' }}
              />
            </div>

            {/* Orbital dots */}
            <motion.div className="absolute" style={{ width: '100%', height: '100%' }}
              animate={{ rotate: 360 }}
              transition={{ duration: 8, repeat: Infinity, ease: 'linear' }}>
              {ORB_ANGLES.map((deg, i) => {
                const rad = (deg * Math.PI) / 180
                return (
                  <div key={i} className="absolute rounded-full"
                    style={{
                      width: i % 2 === 0 ? 6 : 4, height: i % 2 === 0 ? 6 : 4,
                      background: 'rgba(0,230,255,0.95)',
                      boxShadow: i % 2 === 0 ? '0 0 10px rgba(0,220,255,1)' : 'none',
                      left: 160 + Math.cos(rad) * 128 - (i % 2 === 0 ? 3 : 2),
                      top:  160 + Math.sin(rad) * 128 - (i % 2 === 0 ? 3 : 2),
                    }}
                  />
                )
              })}
            </motion.div>

            {/* Core sphere */}
            <motion.div className="relative z-10 rounded-full"
              style={{
                width: 105, height: 105,
                background: 'radial-gradient(circle at 32% 28%, rgba(130,235,255,0.95) 0%, rgba(0,160,230,0.85) 40%, rgba(0,40,90,0.97) 100%)',
              }}
              initial={{ scale: 0 }}
              animate={{
                scale: 1,
                boxShadow: [
                  '0 0 50px rgba(0,200,255,0.6), 0 0 100px rgba(0,180,255,0.25)',
                  '0 0 80px rgba(0,220,255,0.9), 0 0 150px rgba(0,200,255,0.4)',
                  '0 0 50px rgba(0,200,255,0.6), 0 0 100px rgba(0,180,255,0.25)',
                ],
              }}
              transition={{
                scale: { duration: 0.7, ease: [0.16, 1, 0.3, 1] },
                boxShadow: { duration: 2.5, repeat: Infinity, ease: 'easeInOut' },
              }}
            >
              <div className="absolute rounded-full" style={{ width: 30, height: 20, background: 'radial-gradient(circle, rgba(255,255,255,0.7) 0%, transparent 100%)', top: 15, left: 18 }} />
            </motion.div>
          </div>

          {/* Text */}
          <motion.div className="text-center mt-6 pointer-events-none"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.6, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}>
            <div className="text-[9px] font-mono tracking-[8px] uppercase mb-3" style={{ color: 'rgba(0,190,220,0.4)' }}>
              NEURAL CORE ONLINE
            </div>
            <div
              className="text-[36px] font-black tracking-tight leading-tight"
              style={{
                color: 'rgba(210,245,255,0.98)',
                textShadow: '0 0 40px rgba(0,200,255,0.4)',
                fontFamily: 'monospace',
              }}
            >
              XYRON IS NOW IN CONTROL
            </div>
            <div className="text-[13px] font-mono tracking-[4px] mt-2" style={{ color: 'rgba(0,170,200,0.55)' }}>
              {systemLine}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
