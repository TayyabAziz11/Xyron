'use client'

import { motion, AnimatePresence } from 'framer-motion'
import type { TakeoverPhase } from '../../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase }

// Global network node positions (percentage of screen)
const NODES = [
  { x: 12, y: 22, label: 'LON' },
  { x: 28, y: 18, label: 'NYC' },
  { x: 50, y: 50, label: 'CORE', center: true },
  { x: 72, y: 20, label: 'TKY' },
  { x: 88, y: 30, label: 'SYD' },
  { x: 18, y: 68, label: 'SAO' },
  { x: 45, y: 25, label: 'FRA' },
  { x: 62, y: 58, label: 'SGP' },
  { x: 82, y: 65, label: 'MEL' },
  { x: 35, y: 75, label: 'JNB' },
]

// Edge pairs (indices)
const EDGES = [
  [0,4],[0,2],[1,2],[1,4],[2,3],[2,5],[2,6],[2,7],[2,8],[2,9],[3,4],[6,3],[7,8],[5,9]
]

export default function SyncPhase({ phase }: Props) {
  const active = phase === 'sync'

  return (
    <AnimatePresence>
      {active && (
        <motion.div
          key="sync-phase"
          className="fixed inset-0 z-[61] flex flex-col items-center justify-center"
          style={{ background: 'rgba(0,2,8,0.90)' }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.35 }}
        >
          {/* Network map SVG */}
          <div className="absolute inset-0">
            <svg className="w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
              <defs>
                <filter id="node-glow" x="-100%" y="-100%" width="300%" height="300%">
                  <feGaussianBlur stdDeviation="0.8" result="blur" />
                  <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
                </filter>
              </defs>

              {/* Edges */}
              {EDGES.map(([a, b], i) => (
                <motion.line
                  key={i}
                  x1={NODES[a].x} y1={NODES[a].y}
                  x2={NODES[b].x} y2={NODES[b].y}
                  stroke="rgba(0,180,220,0.35)"
                  strokeWidth="0.12"
                  initial={{ pathLength: 0, opacity: 0 }}
                  animate={{ pathLength: 1, opacity: [0, 0.5, 0.2, 0.5] }}
                  transition={{
                    pathLength: { duration: 0.6, delay: i * 0.08 },
                    opacity: { duration: 2, delay: i * 0.08, repeat: Infinity },
                  }}
                />
              ))}

              {/* Data packets traveling on edges */}
              {EDGES.slice(0, 6).map(([a, b], i) => (
                <motion.circle key={`pkt-${i}`} r="0.4"
                  fill="rgba(0,200,255,0.9)"
                  filter="url(#node-glow)"
                  animate={{
                    cx: [NODES[a].x, NODES[b].x],
                    cy: [NODES[a].y, NODES[b].y],
                    opacity: [0, 1, 1, 0],
                  }}
                  transition={{ duration: 1.2, delay: i * 0.35, repeat: Infinity, ease: 'linear' }}
                />
              ))}

              {/* Nodes */}
              {NODES.map((n, i) => (
                <g key={i}>
                  <motion.circle cx={n.x} cy={n.y} r={n.center ? 1.8 : 0.9}
                    fill={n.center ? 'rgba(0,200,255,0.9)' : 'rgba(0,140,180,0.7)'}
                    filter="url(#node-glow)"
                    initial={{ r: 0, opacity: 0 }}
                    animate={{
                      r: n.center ? [0, 2.2, 1.8] : [0, 1.1, 0.9],
                      opacity: 1,
                    }}
                    transition={{ duration: 0.4, delay: i * 0.06 }}
                  />
                  {n.center && (
                    <motion.circle cx={n.x} cy={n.y} r="4"
                      stroke="rgba(0,200,255,0.25)" strokeWidth="0.3" fill="none"
                      animate={{ r: [1.8, 6, 1.8], opacity: [0.6, 0, 0.6] }}
                      transition={{ duration: 2.5, repeat: Infinity, ease: 'easeOut' }}
                    />
                  )}
                  <text x={n.x} y={n.y + 1.8} textAnchor="middle"
                    className="select-none"
                    style={{ fontSize: 1.5, fill: 'rgba(0,160,200,0.6)', fontFamily: 'monospace' }}>
                    {n.label}
                  </text>
                </g>
              ))}
            </svg>
          </div>

          {/* Center text overlay */}
          <div className="relative z-10 text-center pointer-events-none">
            <motion.div
              className="text-[10px] font-mono tracking-[7px] uppercase mb-3"
              style={{ color: 'rgba(0,180,220,0.5)' }}
              animate={{ opacity: [0.5, 1, 0.5] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            >
              GLOBAL NEURAL NETWORK
            </motion.div>
            <div
              className="text-[38px] font-black tracking-tight leading-none"
              style={{
                color: 'rgba(0,200,255,0.95)',
                textShadow: '0 0 40px rgba(0,200,255,0.7), 0 0 80px rgba(0,180,255,0.3)',
                fontFamily: 'monospace',
              }}
            >
              SYNCING AI CORE
            </div>
            <motion.div
              className="text-[12px] font-mono tracking-[4px] mt-2"
              style={{ color: 'rgba(0,160,200,0.6)' }}
            >
              CONNECTING GLOBAL NODES...
            </motion.div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
