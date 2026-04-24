import { motion } from 'framer-motion'

const BARS = 20
const HEIGHTS = [8, 16, 24, 32, 20, 36, 14, 28, 38, 22, 38, 28, 14, 36, 20, 32, 24, 16, 8, 12]

interface VoiceWaveProps {
  active: boolean
  mode?: 'listening' | 'speaking' | 'idle'
  className?: string
}

const COLOR_MAP = {
  listening: { from: '#10b981', to: '#34d399' },
  speaking:  { from: '#7c3aed', to: '#a78bfa' },
  idle:      { from: '#334155', to: '#475569' },
}

export default function VoiceWave({ active, mode = 'idle', className = '' }: VoiceWaveProps) {
  const colors = COLOR_MAP[active ? mode : 'idle']

  return (
    <div className={`flex items-center justify-center gap-[2px] ${className}`}>
      {HEIGHTS.map((h, i) => (
        <motion.div
          key={i}
          style={{
            background: `linear-gradient(to top, ${colors.from}, ${colors.to})`,
            width: 3,
            borderRadius: 9999,
            originY: 0.5,
          }}
          animate={active
            ? {
                height: [4, h * (0.7 + Math.random() * 0.6), 4],
                opacity: [0.6, 1, 0.6],
              }
            : { height: 3, opacity: 0.2 }
          }
          transition={active
            ? {
                duration: 0.4 + (i % 4) * 0.1,
                repeat: Infinity,
                delay: i * 0.04,
                ease: 'easeInOut',
              }
            : { duration: 0.4 }
          }
        />
      ))}
    </div>
  )
}
