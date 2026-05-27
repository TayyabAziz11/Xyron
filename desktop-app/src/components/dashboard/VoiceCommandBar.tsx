import { useRef, useState, useCallback } from 'react'
import { Send, Mic, MicOff } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'

interface Props {
  onSubmit?: (text: string) => void
  listening?: boolean
  onMicToggle?: () => void
}

const BARS = 18

export function VoiceCommandBar({ onSubmit, listening = false, onMicToggle }: Props) {
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const submit = useCallback(() => {
    const t = text.trim()
    if (!t) return
    onSubmit?.(t)
    setText('')
    inputRef.current?.focus()
  }, [text, onSubmit])

  const onKey = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  }, [submit])

  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-lg"
      style={{
        background: 'rgba(10,11,16,0.95)',
        border: '1px solid rgba(255,35,45,0.3)',
        boxShadow: '0 0 20px rgba(255,31,45,0.08)',
      }}>

      {/* Mic button */}
      <button
        onClick={onMicToggle}
        className="relative flex-shrink-0 w-9 h-9 rounded-full flex items-center justify-center transition-all"
        style={{
          background: listening ? 'rgba(255,31,45,0.2)' : 'rgba(255,255,255,0.04)',
          border: `1px solid ${listening ? '#ff1f2d' : 'rgba(255,255,255,0.1)'}`,
          boxShadow: listening ? '0 0 12px rgba(255,31,45,0.5)' : 'none',
        }}>
        {listening
          ? <Mic size={14} style={{ color: '#ff1f2d' }} />
          : <MicOff size={14} style={{ color: '#6b7280' }} />}
        {listening && (
          <motion.div className="absolute inset-0 rounded-full"
            animate={{ boxShadow: ['0 0 0px rgba(255,31,45,0)', '0 0 16px rgba(255,31,45,0.6)', '0 0 0px rgba(255,31,45,0)'] }}
            transition={{ duration: 1.2, repeat: Infinity }} />
        )}
      </button>

      {/* Waveform */}
      <AnimatePresence>
        {listening && (
          <motion.div className="flex items-center gap-0.5 flex-shrink-0"
            initial={{ opacity: 0, width: 0 }}
            animate={{ opacity: 1, width: 'auto' }}
            exit={{ opacity: 0, width: 0 }}>
            {Array.from({ length: BARS }, (_, i) => (
              <motion.div key={i}
                className="w-px rounded-full"
                style={{ background: '#ff1f2d', height: 14 }}
                animate={{ scaleY: [0.2, Math.random() * 0.8 + 0.2, 0.2] }}
                transition={{ duration: 0.4 + Math.random() * 0.4, repeat: Infinity, delay: i * 0.04 }} />
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Text input */}
      <input
        ref={inputRef}
        value={text}
        onChange={e => setText(e.target.value)}
        onKeyDown={onKey}
        placeholder={listening ? 'Listening…' : 'Type a command or say "Hey Xyron"…'}
        className="flex-1 bg-transparent font-mono text-[11px] text-[#e8ecf0] placeholder-[#374151] focus:outline-none"
        style={{ minWidth: 0 }}
      />

      {/* Wake word hint */}
      {!listening && !text && (
        <span className="font-mono text-[8px] text-[#374151] flex-shrink-0 hidden sm:block">
          ⌨ or say{' '}
          <span style={{ color: 'rgba(255,31,45,0.5)' }}>"Hey Xyron"</span>
        </span>
      )}

      {/* Send */}
      <button
        onClick={submit}
        disabled={!text.trim()}
        className="flex-shrink-0 w-8 h-8 rounded flex items-center justify-center transition-all disabled:opacity-30"
        style={{
          background: text.trim() ? '#ff1f2d' : 'rgba(255,31,45,0.12)',
          border: '1px solid rgba(255,31,45,0.4)',
          boxShadow: text.trim() ? '0 0 10px rgba(255,31,45,0.5)' : 'none',
        }}>
        <Send size={12} className="text-white" />
      </button>
    </div>
  )
}
