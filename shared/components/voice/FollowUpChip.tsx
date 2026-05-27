

import { motion, AnimatePresence } from 'framer-motion'
import { MessageCircle, X } from 'lucide-react'

interface Props {
  suggestion: string | null
  onDismiss:  () => void
  onAccept?:  (text: string) => void
}

export function FollowUpChip({ suggestion, onDismiss, onAccept }: Props) {
  return (
    <AnimatePresence>
      {suggestion && (
        <motion.div
          initial={{ opacity: 0, y: 8, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -8, scale: 0.95 }}
          transition={{ duration: 0.2 }}
          className="flex items-center gap-2 rounded-xl border border-accent-primary/30 bg-accent-primary/10 px-3 py-2 text-sm"
        >
          <MessageCircle className="h-3.5 w-3.5 shrink-0 text-accent-primary" />
          <span className="flex-1 text-text-secondary">{suggestion}</span>
          {onAccept && (
            <button
              onClick={() => { onAccept(suggestion); onDismiss() }}
              className="rounded-md bg-accent-primary/20 px-2 py-0.5 text-xs font-medium text-accent-primary hover:bg-accent-primary/30 transition-colors"
            >
              Yes
            </button>
          )}
          <button onClick={onDismiss} className="text-text-muted hover:text-text-secondary transition-colors">
            <X className="h-3.5 w-3.5" />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
