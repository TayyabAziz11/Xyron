'use client'

import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/Button'

interface CommandBarProps {
  onSubmit: (text: string) => Promise<void>
  loading?: boolean
  className?: string
}

export function CommandBar({ onSubmit, loading = false, className }: CommandBarProps) {
  const [text, setText] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim() || loading) return
    await onSubmit(text.trim())
    setText('')
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleSubmit(e as unknown as React.FormEvent)
    }
  }

  return (
    <form onSubmit={handleSubmit} className={className}>
      <div className="relative rounded-xl border border-surface-border bg-surface-raised overflow-hidden focus-within:border-brand/50 focus-within:ring-1 focus-within:ring-brand/20 transition-all">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={4}
          placeholder="e.g., Draft a follow-up email to the team about the Monday meeting…"
          className="w-full resize-none bg-transparent px-5 pt-4 pb-14 text-sm text-text-primary placeholder:text-text-muted focus:outline-none"
        />
        <div className="absolute bottom-0 left-0 right-0 flex items-center justify-between border-t border-surface-border bg-surface-overlay/50 px-4 py-2.5">
          <p className="text-xs text-text-muted">
            Press{' '}
            <kbd className="rounded bg-surface-border px-1.5 py-0.5 font-mono text-xs">
              ⌘↵
            </kbd>{' '}
            to run
          </p>
          <Button type="submit" size="sm" loading={loading} icon={ArrowRight}>
            Run Command
          </Button>
        </div>
      </div>
    </form>
  )
}
