import { memo, useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { safeArray, safeString } from '@desktop/lib/dashboardSafeData'
import type { ActivityItem } from '@desktop/services/dashboardApi'

function relativeTime(ts: unknown): string {
  try {
    const d = new Date(safeString(ts, ''))
    if (isNaN(d.getTime())) return 'just now'
    const diff = Date.now() - d.getTime()
    if (diff < 0)       return 'just now'
    if (diff < 60000)   return `${Math.round(diff / 1000)}s ago`
    if (diff < 3600000) return `${Math.round(diff / 60000)}m ago`
    return `${Math.round(diff / 3600000)}h ago`
  } catch {
    return 'just now'
  }
}

function typeColor(type: string): string {
  if (type === 'command')   return '#00e5ff'
  if (type === 'approval')  return '#ff8c00'
  if (type === 'system')    return '#a855f7'
  if (type === 'voice')     return '#00ff88'
  if (type === 'error')     return '#ff1f2d'
  return '#6b7280'
}

interface Props {
  items: ActivityItem[] | unknown
}

export const LiveFeed = memo(function LiveFeed({ items }: Props) {
  const safe      = safeArray<ActivityItem>(items)
  const bottomRef = useRef<HTMLDivElement>(null)
  const [, setTick] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 30000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [safe.length])

  return (
    <div className="flex flex-col gap-2 h-full">
      <div className="flex items-center justify-between">
        <span className="x-section-label">Live Feed</span>
        <div className="flex items-center gap-1">
          <span className="x-live-dot" />
          <span className="font-mono text-[8px] text-[#6b7280]">REAL-TIME</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto flex flex-col gap-1 pr-1" style={{ maxHeight: 220 }}>
        <AnimatePresence initial={false}>
          {safe.length === 0 && (
            <div className="font-mono text-[9px] text-[#374151] py-3 text-center">No activity yet</div>
          )}
          {safe.map((item, idx) => {
            const id    = safeString(item?.id, String(idx))
            const type  = safeString(item?.type, 'command')
            const desc  = safeString(item?.description, '—')
            const color = typeColor(type)

            return (
              <motion.div
                key={id}
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18 }}
                className="flex items-start gap-2 rounded px-2 py-1.5"
                style={{ background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.04)' }}
              >
                <div className="w-1.5 h-1.5 rounded-full mt-1 flex-shrink-0"
                  style={{ background: color, boxShadow: `0 0 4px ${color}` }} />
                <div className="flex-1 min-w-0">
                  <div className="font-mono text-[9px] text-[#9ca3af] truncate">{desc}</div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="font-mono text-[7px] text-[#374151] uppercase tracking-widest"
                      style={{ color }}>
                      {type}
                    </span>
                    <span className="font-mono text-[7px] text-[#374151]">
                      {relativeTime(item?.timestamp)}
                    </span>
                  </div>
                </div>
                {item?.success === false && (
                  <span className="font-mono text-[7px] text-[#ff1f2d] flex-shrink-0">FAIL</span>
                )}
              </motion.div>
            )
          })}
        </AnimatePresence>
        <div ref={bottomRef} />
      </div>
    </div>
  )
})
