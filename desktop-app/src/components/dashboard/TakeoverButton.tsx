import { motion, AnimatePresence } from 'framer-motion'
import { Zap, ZapOff, Loader2 } from 'lucide-react'
import { useTakeoverStatus } from '@desktop/hooks/useTakeoverStatus'
import type { AutonomyLevel } from '@desktop/services/takeoverApi'

function formatAutonomyLevel(value: AutonomyLevel): string {
  if (typeof value === 'string') return value.toUpperCase()
  if (typeof value === 'number') return `LEVEL ${value}`
  if (value === null || value === undefined) return 'UNKNOWN'
  return String(value).toUpperCase()
}

export function TakeoverButton() {
  const { status, toggling, error, toggle } = useTakeoverStatus()

  const safeStatus = {
    active:         Boolean(status?.active),
    autonomy_level: status?.autonomy_level ?? null,
    source:         status?.source ?? 'system',
    started_at:     status?.started_at ?? null,
    last_action:    status?.last_action ?? null,
  }

  const active = safeStatus.active

  return (
    <div className="flex flex-col gap-1">
      {/* Main button */}
      <button
        onClick={toggle}
        disabled={toggling}
        className="relative flex items-center justify-center gap-2 w-full py-2.5 rounded-lg font-mono text-[10px] font-bold tracking-widest uppercase transition-all disabled:opacity-60"
        style={{
          background: active
            ? 'rgba(255,31,45,0.18)'
            : 'rgba(255,255,255,0.04)',
          border: `1px solid ${active ? '#ff1f2d' : 'rgba(255,255,255,0.1)'}`,
        }}>

        {/* Animated glow ring when active */}
        <AnimatePresence>
          {active && (
            <motion.div
              className="absolute inset-0 rounded-lg pointer-events-none"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1,
                boxShadow: [
                  '0 0 0 1px rgba(255,31,45,0.4), 0 0 14px rgba(255,31,45,0.25)',
                  '0 0 0 2px rgba(255,31,45,0.8), 0 0 28px rgba(255,31,45,0.5)',
                  '0 0 0 1px rgba(255,31,45,0.4), 0 0 14px rgba(255,31,45,0.25)',
                ],
              }}
              exit={{ opacity: 0 }}
              transition={{ duration: 1.5, repeat: Infinity }} />
          )}
        </AnimatePresence>

        {toggling
          ? <Loader2 size={13} className="animate-spin text-[#ff1f2d]" />
          : active
            ? <Zap size={13} style={{ color: '#ff1f2d', filter: 'drop-shadow(0 0 4px #ff1f2d)' }} />
            : <ZapOff size={13} style={{ color: '#6b7280' }} />}

        <span style={{ color: active ? '#ff1f2d' : '#6b7280' }}>
          {toggling ? 'SWITCHING…' : active ? 'TAKEOVER ACTIVE' : 'ENABLE TAKEOVER'}
        </span>
      </button>

      {/* Status info */}
      {status ? (
        <div className="flex items-center justify-between px-1">
          <span className="font-mono text-[7px] text-[#374151]">
            Level: <span style={{ color: active ? '#ff8c00' : '#374151' }}>
              {formatAutonomyLevel(safeStatus.autonomy_level)}
            </span>
          </span>
          {safeStatus.last_action && (
            <span className="font-mono text-[7px] text-[#374151] truncate max-w-[120px]">
              {safeStatus.last_action}
            </span>
          )}
        </div>
      ) : !error && (
        <div className="font-mono text-[7px] text-[#374151] px-1">TAKEOVER STATUS UNKNOWN</div>
      )}

      {error && (
        <div className="font-mono text-[7px] text-[#ff1f2d] px-1">{error}</div>
      )}
    </div>
  )
}
