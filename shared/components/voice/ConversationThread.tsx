

import { useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Bot, User, Loader2, Info } from 'lucide-react'
import type { ConvMessage } from '@/hooks/useVoiceSession'

interface Props {
  messages: ConvMessage[]
}

export function ConversationThread({ messages }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = bottomRef.current?.parentElement
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length])

  if (!messages.length) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center select-none">
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full border border-brand/20 bg-brand/10">
          <Bot className="h-6 w-6 text-brand-light" />
        </div>
        <p className="text-sm font-medium text-text-secondary mb-1">Ready to assist</p>
        <p className="text-xs text-text-muted">Start a voice session to begin the conversation</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4 py-4 px-1">
      <AnimatePresence initial={false}>
        {messages.map((msg) => (
          <motion.div
            key={msg.id}
            initial={{ opacity: 0, y: 10, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.22, ease: [0.25, 0.1, 0.25, 1] }}
            className={
              msg.role === 'system'
                ? 'flex justify-center'
                : msg.role === 'user'
                ? 'flex justify-end items-end gap-2'
                : 'flex justify-start items-end gap-2'
            }
          >
            {/* System message */}
            {msg.role === 'system' && (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-surface-border bg-surface-overlay px-3 py-1 text-xs text-text-muted">
                <Info className="h-3 w-3 shrink-0" />
                {msg.text}
              </span>
            )}

            {/* User message */}
            {msg.role === 'user' && (
              <>
                <div className="max-w-[75%] rounded-2xl rounded-br-sm bg-brand px-4 py-2.5 text-sm text-white leading-relaxed shadow-sm shadow-brand/20">
                  {msg.text}
                </div>
                <div className="mb-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-overlay border border-surface-border">
                  <User className="h-3.5 w-3.5 text-text-muted" />
                </div>
              </>
            )}

            {/* Assistant message */}
            {msg.role === 'assistant' && (
              <>
                <div className="mb-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand/15 border border-brand/25">
                  <Bot className="h-3.5 w-3.5 text-brand-light" />
                </div>
                <div
                  className={[
                    'max-w-[75%] rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm leading-relaxed',
                    msg.status === 'error'
                      ? 'bg-status-error/10 border border-status-error/20 text-status-error'
                      : 'bg-surface-raised border border-surface-border text-text-primary',
                  ].join(' ')}
                >
                  {msg.status === 'processing' ? (
                    <span className="flex items-center gap-2 text-text-muted">
                      <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
                      Processing…
                    </span>
                  ) : (
                    msg.text || '…'
                  )}
                </div>
              </>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
      <div ref={bottomRef} />
    </div>
  )
}
