'use client'

import { motion, AnimatePresence } from 'framer-motion'
import { Bell, Calendar, Coffee, X } from 'lucide-react'
import type { ProactiveNotification } from '@/hooks/useProactive'

const ICONS = {
  meeting:  Calendar,
  break:    Coffee,
  reminder: Bell,
  tip:      Bell,
  info:     Bell,
}

const COLORS = {
  meeting:  'border-blue-500/30 bg-blue-500/10 text-blue-400',
  break:    'border-green-500/30 bg-green-500/10 text-green-400',
  reminder: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  tip:      'border-yellow-500/30 bg-yellow-500/10 text-yellow-400',
  info:     'border-text-muted/30 bg-surface-secondary/50 text-text-secondary',
}

interface Props {
  notifications: ProactiveNotification[]
  onDismiss:     (id: number) => void
}

export function ProactiveToast({ notifications, onDismiss }: Props) {
  if (!notifications.length) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      <AnimatePresence>
        {notifications.slice(0, 3).map((n) => {
          const Icon  = ICONS[n.category] ?? Bell
          const color = COLORS[n.category] ?? COLORS.info
          return (
            <motion.div
              key={n.id}
              initial={{ opacity: 0, x: 40, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, scale: 0.95 }}
              transition={{ duration: 0.25 }}
              className={`flex items-start gap-3 rounded-xl border p-3 shadow-lg ${color}`}
            >
              <Icon className="mt-0.5 h-4 w-4 shrink-0" />
              <p className="flex-1 text-sm leading-snug">{n.message}</p>
              <button
                onClick={() => onDismiss(n.id)}
                className="mt-0.5 opacity-60 hover:opacity-100 transition-opacity"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
