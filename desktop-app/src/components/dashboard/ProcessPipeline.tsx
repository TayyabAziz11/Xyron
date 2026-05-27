import { memo } from 'react'
import { motion } from 'framer-motion'
import { ChevronRight } from 'lucide-react'

const STAGES = [
  { id: 'INPUT',    label: 'INPUT',    color: '#00e5ff' },
  { id: 'NLP',      label: 'NLP',      color: '#a855f7' },
  { id: 'PLANNER',  label: 'PLANNER',  color: '#ff8c00' },
  { id: 'EXECUTOR', label: 'EXECUTOR', color: '#ff1f2d' },
  { id: 'FEEDBACK', label: 'FEEDBACK', color: '#00ff88' },
]

interface Props {
  activeStage?: string   // matches STAGES[].id — animated highlight
  processing?: boolean
}

export const ProcessPipeline = memo(function ProcessPipeline({ activeStage, processing = false }: Props) {
  const activeIdx = STAGES.findIndex(s => s.id === activeStage)

  return (
    <div className="flex flex-col gap-2">
      <span className="x-section-label">Active Process Pipeline</span>

      <div className="flex items-center gap-1 overflow-x-auto py-1">
        {STAGES.map((stage, i) => {
          const isActive  = stage.id === activeStage
          const isPast    = activeIdx >= 0 && i < activeIdx
          const isPending = activeIdx < 0 || i > activeIdx

          return (
            <div key={stage.id} className="flex items-center gap-1 flex-shrink-0">
              {/* Stage node */}
              <motion.div
                animate={isActive && processing
                  ? { boxShadow: [`0 0 0px ${stage.color}00`, `0 0 12px ${stage.color}cc`, `0 0 0px ${stage.color}00`] }
                  : {}}
                transition={{ duration: 1.2, repeat: Infinity }}
                className="relative px-2.5 py-1 rounded font-mono text-[9px] font-semibold tracking-widest uppercase"
                style={{
                  background: isActive
                    ? `${stage.color}22`
                    : isPast
                      ? `${stage.color}10`
                      : 'rgba(255,255,255,0.03)',
                  border: `1px solid ${isActive ? stage.color : isPast ? `${stage.color}44` : 'rgba(255,255,255,0.08)'}`,
                  color: isActive ? stage.color : isPast ? `${stage.color}88` : '#374151',
                  transition: 'all 0.3s ease',
                }}
              >
                {stage.label}
                {isActive && processing && (
                  <motion.div
                    className="absolute -bottom-0.5 left-0 h-px"
                    style={{ background: stage.color }}
                    animate={{ width: ['0%', '100%'] }}
                    transition={{ duration: 1.5, repeat: Infinity, ease: 'linear' }}
                  />
                )}
              </motion.div>

              {i < STAGES.length - 1 && (
                <ChevronRight size={10}
                  style={{ color: isPast ? `${STAGES[i+1].color}66` : 'rgba(255,255,255,0.12)', flexShrink: 0 }} />
              )}
            </div>
          )
        })}
      </div>

      {/* Status line */}
      <div className="flex items-center gap-2">
        <div className="x-live-dot" style={
          processing
            ? {}
            : { background: '#374151', boxShadow: 'none', animation: 'none' }
        } />
        <span className="font-mono text-[8px] text-[#6b7280]">
          {processing && activeStage
            ? `Processing via ${activeStage} module…`
            : 'Pipeline idle — awaiting input'}
        </span>
      </div>
    </div>
  )
})
