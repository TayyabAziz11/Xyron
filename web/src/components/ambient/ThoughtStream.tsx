'use client'

import { useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { getEmotion, type Thought } from '@/config/emotions'

interface ThoughtStreamProps {
  thoughts: Thought[]
  visible: boolean
}

function formatTime(ts: number): string {
  const d = new Date(ts)
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export default function ThoughtStream({ thoughts, visible }: ThoughtStreamProps) {
  const bottomRef   = useRef<HTMLDivElement>(null)
  const displayed   = thoughts.slice(-20)

  useEffect(() => {
    if (visible && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [thoughts, visible])

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          className="fixed right-0 top-0 z-40 flex h-screen w-[300px] flex-col border-l border-white/10 bg-[#020508]/90 backdrop-blur-xl"
          initial={{ x: 300, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: 300, opacity: 0 }}
          transition={{ type: 'spring', damping: 28, stiffness: 220 }}
        >
          {/* Header */}
          <div className="flex flex-shrink-0 items-center justify-between border-b border-white/8 px-4 py-3">
            <div className="flex items-center gap-2">
              <motion.div
                className="h-1.5 w-1.5 rounded-full bg-emerald-400"
                animate={{ opacity: [1, 0.3, 1] }}
                transition={{ duration: 2, repeat: Infinity }}
              />
              <span className="font-mono text-[10px] font-bold tracking-[0.2em] text-white/60">
                THOUGHT STREAM
              </span>
            </div>
            <span className="font-mono text-[8px] text-white/25">{displayed.length}/20</span>
          </div>

          {/* Thoughts list */}
          <div className="flex-1 overflow-y-auto px-3 py-3 no-scrollbar">
            {displayed.length === 0 ? (
              <div className="mt-8 flex flex-col items-center gap-2">
                <span className="text-2xl">💭</span>
                <p className="font-mono text-[9px] text-white/20">
                  warming up...
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {displayed.map((thought, i) => {
                  const fromBottom    = displayed.length - 1 - i
                  const targetOpacity = Math.max(0.25, 1 - fromBottom * 0.04)
                  const cfg           = getEmotion(thought.emotion)

                  return (
                    <motion.div
                      key={thought.id}
                      initial={{ opacity: 0, y: 12, scale: 0.97 }}
                      animate={{ opacity: targetOpacity, y: 0, scale: 1 }}
                      transition={{ duration: 0.35, ease: 'easeOut' }}
                      className="group relative rounded-lg border px-3 py-2.5"
                      style={{
                        borderColor: `${cfg.color}22`,
                        background:  `${cfg.color}08`,
                      }}
                    >
                      {/* Emotion tag */}
                      <div className="mb-1.5 flex items-center gap-1.5">
                        <span className="text-[11px] leading-none">{cfg.emoji}</span>
                        <span
                          className="font-mono text-[8px] font-bold tracking-widest"
                          style={{ color: cfg.color }}
                        >
                          {cfg.label.toUpperCase()}
                        </span>
                        <span className="ml-auto font-mono text-[8px] text-white/20">
                          {formatTime(thought.timestamp)}
                        </span>
                      </div>

                      {/* Thought text */}
                      <p className="font-mono text-[10px] leading-relaxed text-white/80">
                        {thought.text}
                      </p>

                      {/* Left accent line */}
                      <div
                        className="absolute left-0 top-2 bottom-2 w-[2px] rounded-full"
                        style={{ background: cfg.color, opacity: 0.5 }}
                      />
                    </motion.div>
                  )
                })}
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Footer: live indicator */}
          <div className="flex flex-shrink-0 items-center gap-2 border-t border-white/8 px-4 py-2.5">
            <motion.div
              className="h-1 w-1 rounded-full bg-emerald-400"
              animate={{ scale: [1, 1.5, 1] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            />
            <span className="font-mono text-[8px] text-white/25">live · inner monologue</span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
