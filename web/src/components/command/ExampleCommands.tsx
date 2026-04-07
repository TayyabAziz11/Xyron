'use client'

import { cn } from '@/lib/utils'

const EXAMPLES = [
  'Summarize my unread emails',
  'Draft a LinkedIn post about our product update',
  'Show me pending invoices in Odoo',
  'Create a follow-up email to John',
  'Check my WhatsApp messages',
  'Generate a weekly activity summary',
]

interface ExampleCommandsProps {
  onSelect: (text: string) => void
  className?: string
}

export function ExampleCommands({ onSelect, className }: ExampleCommandsProps) {
  return (
    <div className={cn('space-y-3', className)}>
      <p className="text-xs font-medium uppercase tracking-wider text-text-muted">
        Example commands
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            onClick={() => onSelect(example)}
            className="rounded-lg border border-surface-border bg-surface-raised px-3 py-3 text-left text-xs text-text-secondary hover:border-brand/30 hover:bg-surface-hover hover:text-text-primary transition-colors"
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  )
}
