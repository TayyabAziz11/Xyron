

import { useState } from 'react'
import { cn } from '@/lib/utils'

interface TooltipProps {
  content: string
  children: React.ReactNode
  className?: string
}

export function Tooltip({ content, children, className }: TooltipProps) {
  const [visible, setVisible] = useState(false)

  return (
    <div
      className={cn('relative inline-flex', className)}
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
    >
      {children}
      {visible && (
        <div className="absolute bottom-full left-1/2 mb-2 -translate-x-1/2 animate-fade-in z-50">
          <div className="rounded-md bg-surface-overlay border border-surface-border px-2.5 py-1.5 text-xs text-text-primary whitespace-nowrap shadow-lg">
            {content}
          </div>
        </div>
      )}
    </div>
  )
}
